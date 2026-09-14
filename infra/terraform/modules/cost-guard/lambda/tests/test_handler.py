"""Unit tests for the cost-guard kill switch. All AWS calls are mocked.

The properties that matter most: a dry run never mutates, and nothing is
touched unless it passes every ownership signal (name, tags, Terraform state).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import handler  # noqa: E402

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
ACCOUNT = "123456789012"
SERVICE_ARN = f"arn:aws:ecs:us-west-2:{ACCOUNT}:service/ekba-dev/ekba-dev"
ALB_ARN = f"arn:aws:elasticloadbalancing:us-west-2:{ACCOUNT}:loadbalancer/app/ekba-dev-alb/abc123"
DB_ARN = f"arn:aws:rds:us-west-2:{ACCOUNT}:db:ekba-dev-postgres"
TRIGGER_TOPIC = f"arn:aws:sns:us-west-2:{ACCOUNT}:ekba-dev-cost-guard-trigger"
OWNED_TAGS = {"ProjectCode": "ekba", "Environment": "dev", "Lifecycle": "ephemeral"}


def make_cfg(dry_run: bool = False) -> handler.Config:
    return handler.Config(
        dry_run=dry_run,
        project_code="ekba",
        environment="dev",
        state_bucket="ekba-tfstate-test",
        state_key="dev/terraform.tfstate",
        trigger_topic_arn=TRIGGER_TOPIC,
        notify_topic_arn=f"arn:aws:sns:us-west-2:{ACCOUNT}:ekba-dev-cost-guard-notify",
        max_session_hours=8,
    )


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


def make_clients(
    resources: list[tuple[str, dict[str, str]]] | None = None,
    state_arns: list[str] | None = None,
    created: datetime | None = None,
    desired: int = 1,
    db_status: str = "available",
) -> handler.Clients:
    resources = (
        [(SERVICE_ARN, OWNED_TAGS), (ALB_ARN, OWNED_TAGS)] if resources is None else resources
    )
    state_arns = [SERVICE_ARN, ALB_ARN] if state_arns is None else state_arns
    created = created or NOW - timedelta(hours=1)

    tagging = MagicMock()
    tagging.get_paginator.return_value.paginate.return_value = [
        {
            "ResourceTagMappingList": [
                {"ResourceARN": arn, "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]}
                for arn, tags in resources
            ]
        }
    ]

    state_json = "{" + ",".join(f'"id": "{a}"' for a in state_arns) + "}"
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: state_json.encode())}

    ecs = MagicMock()
    ecs.describe_services.return_value = {
        "services": [
            {
                "status": "ACTIVE",
                "desiredCount": desired,
                "runningCount": desired,
                "pendingCount": 0,
                "createdAt": created,
            }
        ]
    }

    elbv2 = MagicMock()
    elbv2.describe_load_balancers.return_value = {"LoadBalancers": [{"CreatedTime": created}]}

    rds = MagicMock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [{"DBInstanceStatus": db_status, "InstanceCreateTime": created}]
    }

    return handler.Clients(tagging=tagging, ecs=ecs, elbv2=elbv2, rds=rds, s3=s3, sns=MagicMock())


def budget_event(topic: str = TRIGGER_TOPIC) -> dict[str, Any]:
    return {
        "Records": [
            {
                "EventSource": "aws:sns",
                "Sns": {
                    "TopicArn": topic,
                    "Subject": "AWS Budgets: ekba-dev-credit-guard has exceeded a threshold",
                    "Message": "AWS Budget Notification",
                },
            }
        ]
    }


def assert_nothing_mutated(clients: handler.Clients) -> None:
    clients.ecs.update_service.assert_not_called()
    clients.elbv2.delete_load_balancer.assert_not_called()
    clients.rds.stop_db_instance.assert_not_called()
    clients.rds.delete_db_instance.assert_not_called()


def published_message(clients: handler.Clients) -> str:
    return str(clients.sns.publish.call_args.kwargs["Message"])


# ---------------------------------------------------------------------------
# Trigger handling
# ---------------------------------------------------------------------------
def test_unknown_event_is_ignored_without_any_aws_call() -> None:
    clients = make_clients()
    result = handler.run({"foo": "bar"}, make_cfg(), clients, NOW)
    assert result["status"] == "ignored"
    clients.tagging.get_paginator.assert_not_called()
    assert_nothing_mutated(clients)


def test_sns_message_from_another_topic_is_ignored() -> None:
    clients = make_clients()
    other = f"arn:aws:sns:us-west-2:{ACCOUNT}:someone-elses-topic"
    result = handler.run(budget_event(other), make_cfg(), clients, NOW)
    assert result["status"] == "ignored"
    assert_nothing_mutated(clients)


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------
def test_budget_trigger_in_dry_run_changes_nothing_but_reports_the_plan() -> None:
    clients = make_clients()
    result = handler.run(budget_event(), make_cfg(dry_run=True), clients, NOW)
    assert result["status"] == "dry-run"
    assert_nothing_mutated(clients)
    message = published_message(clients)
    assert "DRY RUN" in message
    assert "WOULD scale ECS service ekba-dev/ekba-dev from 1 to 0 tasks" in message
    assert "WOULD delete load balancer ekba-dev-alb" in message


@pytest.mark.parametrize("value", [None, "", "true", "0", "no", "False "])
def test_dry_run_is_on_unless_explicitly_false(
    value: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = {
        "PROJECT_CODE": "ekba",
        "ENVIRONMENT": "dev",
        "STATE_BUCKET": "b",
        "STATE_KEY": "k",
        "TRIGGER_TOPIC_ARN": "t",
        "NOTIFY_TOPIC_ARN": "n",
        "MAX_SESSION_HOURS": "8",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if value is None:
        monkeypatch.delenv("DRY_RUN", raising=False)
    else:
        monkeypatch.setenv("DRY_RUN", value)
    # "False " (stripped, case-insensitive) is the only explicit opt-out here.
    assert handler.Config.from_env().dry_run is (value != "False ")


# ---------------------------------------------------------------------------
# Live shutdown
# ---------------------------------------------------------------------------
def test_budget_trigger_live_scales_service_to_zero_and_deletes_alb() -> None:
    clients = make_clients()
    result = handler.run(budget_event(), make_cfg(), clients, NOW)
    assert result["status"] == "shut-down"
    clients.ecs.update_service.assert_called_once_with(
        cluster="ekba-dev", service=SERVICE_ARN, desiredCount=0
    )
    clients.elbv2.delete_load_balancer.assert_called_once_with(LoadBalancerArn=ALB_ARN)


def test_already_stopped_environment_is_a_no_op() -> None:
    clients = make_clients(resources=[(SERVICE_ARN, OWNED_TAGS)], desired=0)
    result = handler.run({"trigger": "manual"}, make_cfg(), clients, NOW)
    assert result["actions"] == []
    assert_nothing_mutated(clients)
    assert "nothing billable was running" in published_message(clients)


def test_one_failure_does_not_stop_the_other_shutdown_and_is_raised() -> None:
    clients = make_clients()
    clients.ecs.update_service.side_effect = client_error("ThrottlingException")
    with pytest.raises(RuntimeError, match="ThrottlingException"):
        handler.run(budget_event(), make_cfg(), clients, NOW)
    clients.elbv2.delete_load_balancer.assert_called_once_with(LoadBalancerArn=ALB_ARN)
    assert "shutdown FAILED" in clients.sns.publish.call_args.kwargs["Subject"]


# ---------------------------------------------------------------------------
# Ownership - every signal must agree
# ---------------------------------------------------------------------------
def test_resource_missing_from_terraform_state_is_not_touched() -> None:
    clients = make_clients(state_arns=[SERVICE_ARN])
    result = handler.run(budget_event(), make_cfg(), clients, NOW)
    clients.elbv2.delete_load_balancer.assert_not_called()
    assert any(ALB_ARN in r and "not in Terraform state" in r for r in result["refused"])
    assert "failed ownership checks" in published_message(clients)


@pytest.mark.parametrize(
    "arn",
    [
        f"arn:aws:elasticloadbalancing:us-west-2:{ACCOUNT}:loadbalancer/app/ekba-devx-alb/x",
        f"arn:aws:elasticloadbalancing:us-west-2:{ACCOUNT}:loadbalancer/app/other-app-alb/x",
        f"arn:aws:ecs:us-west-2:{ACCOUNT}:service/other-cluster/ekba-dev",
        f"arn:aws:ecs:us-west-2:{ACCOUNT}:service/ekba-dev/other-service",
    ],
)
def test_resource_without_project_name_prefix_is_not_touched(arn: str) -> None:
    clients = make_clients(resources=[(arn, OWNED_TAGS)], state_arns=[arn])
    result = handler.run(budget_event(), make_cfg(), clients, NOW)
    assert_nothing_mutated(clients)
    assert result["refused"] and "prefix" in result["refused"][0]


@pytest.mark.parametrize(
    "tags",
    [
        {**OWNED_TAGS, "Lifecycle": "protected"},
        {**OWNED_TAGS, "ProjectCode": "other"},
        {**OWNED_TAGS, "Environment": "prod"},
        {"ProjectCode": "ekba", "Environment": "dev"},
    ],
)
def test_resource_with_wrong_or_missing_tags_is_not_touched(tags: dict[str, str]) -> None:
    clients = make_clients(resources=[(ALB_ARN, tags)])
    result = handler.run(budget_event(), make_cfg(), clients, NOW)
    assert_nothing_mutated(clients)
    assert result["refused"] and "tag" in result["refused"][0]


def test_unreadable_state_touches_nothing_and_fails_loudly() -> None:
    clients = make_clients()
    clients.s3.get_object.side_effect = client_error("AccessDenied")
    with pytest.raises(RuntimeError, match="could not read Terraform state"):
        handler.run(budget_event(), make_cfg(), clients, NOW)
    assert_nothing_mutated(clients)
    clients.sns.publish.assert_called_once()


def test_missing_state_means_nothing_is_owned() -> None:
    clients = make_clients()
    clients.s3.get_object.side_effect = client_error("NoSuchKey")
    result = handler.run(budget_event(), make_cfg(), clients, NOW)
    assert_nothing_mutated(clients)
    assert len(result["refused"]) == 2


# ---------------------------------------------------------------------------
# Session limit
# ---------------------------------------------------------------------------
def test_session_within_limit_does_nothing_and_sends_no_email() -> None:
    clients = make_clients(created=NOW - timedelta(hours=7, minutes=59))
    result = handler.run({"trigger": "session-limit"}, make_cfg(), clients, NOW)
    assert result["status"] == "within-session-limit"
    assert_nothing_mutated(clients)
    clients.sns.publish.assert_not_called()


def test_session_over_limit_shuts_down_when_live() -> None:
    clients = make_clients(created=NOW - timedelta(hours=8, minutes=1))
    result = handler.run({"trigger": "session-limit"}, make_cfg(), clients, NOW)
    assert result["status"] == "shut-down"
    assert "limit is 8h" in result["reason"]
    clients.ecs.update_service.assert_called_once()
    clients.elbv2.delete_load_balancer.assert_called_once()
    clients.sns.publish.assert_called_once()


def test_session_over_limit_in_dry_run_only_logs() -> None:
    clients = make_clients(created=NOW - timedelta(hours=20))
    result = handler.run({"trigger": "session-limit"}, make_cfg(dry_run=True), clients, NOW)
    assert result["status"] == "dry-run"
    assert_nothing_mutated(clients)
    clients.sns.publish.assert_not_called()


def test_session_check_with_nothing_running_is_quiet() -> None:
    clients = make_clients(resources=[(SERVICE_ARN, OWNED_TAGS)], desired=0)
    result = handler.run({"trigger": "session-limit"}, make_cfg(), clients, NOW)
    assert result["status"] == "nothing-running"
    clients.sns.publish.assert_not_called()


# ---------------------------------------------------------------------------
# RDS - stopped (never deleted), under the same ownership rules
# ---------------------------------------------------------------------------
ALL_OWNED = [(SERVICE_ARN, OWNED_TAGS), (ALB_ARN, OWNED_TAGS), (DB_ARN, OWNED_TAGS)]
ALL_ARNS = [SERVICE_ARN, ALB_ARN, DB_ARN]


def test_rds_arn_is_parsed_and_other_rds_types_are_not() -> None:
    assert handler.parse_names(DB_ARN) == ("db-instance", ["ekba-dev-postgres"])
    assert handler.parse_names(f"arn:aws:rds:us-west-2:{ACCOUNT}:cluster:ekba-dev-aurora") is None
    assert (
        handler.parse_names(f"arn:aws:rds:us-west-2:{ACCOUNT}:snapshot:ekba-dev-postgres-1") is None
    )


def test_dry_run_reports_it_would_stop_the_database_and_changes_nothing() -> None:
    clients = make_clients(resources=ALL_OWNED, state_arns=ALL_ARNS)
    result = handler.run(budget_event(), make_cfg(dry_run=True), clients, NOW)
    assert result["status"] == "dry-run"
    assert_nothing_mutated(clients)
    assert "WOULD stop RDS instance ekba-dev-postgres" in published_message(clients)


def test_live_shutdown_stops_the_database_last_and_never_deletes_it() -> None:
    calls: list[str] = []
    clients = make_clients(resources=ALL_OWNED, state_arns=ALL_ARNS)
    clients.ecs.update_service.side_effect = lambda **_: calls.append("ecs")
    clients.elbv2.delete_load_balancer.side_effect = lambda **_: calls.append("alb")
    clients.rds.stop_db_instance.side_effect = lambda **_: calls.append("rds")

    result = handler.run(budget_event(), make_cfg(), clients, NOW)

    assert result["status"] == "shut-down"
    assert calls == ["ecs", "alb", "rds"]
    clients.rds.stop_db_instance.assert_called_once_with(DBInstanceIdentifier="ekba-dev-postgres")
    clients.rds.delete_db_instance.assert_not_called()


@pytest.mark.parametrize("status", ["stopped", "stopping", "deleting"])
def test_database_that_is_not_running_is_left_alone(status: str) -> None:
    clients = make_clients(resources=[(DB_ARN, OWNED_TAGS)], state_arns=[DB_ARN], db_status=status)
    result = handler.run({"trigger": "manual"}, make_cfg(), clients, NOW)
    assert result["actions"] == []
    assert_nothing_mutated(clients)


def test_database_missing_from_terraform_state_is_not_touched() -> None:
    clients = make_clients(resources=[(DB_ARN, OWNED_TAGS)], state_arns=[])
    result = handler.run(budget_event(), make_cfg(), clients, NOW)
    assert_nothing_mutated(clients)
    assert any(DB_ARN in r and "not in Terraform state" in r for r in result["refused"])


@pytest.mark.parametrize(
    "arn",
    [
        f"arn:aws:rds:us-west-2:{ACCOUNT}:db:ekba-devx-postgres",
        f"arn:aws:rds:us-west-2:{ACCOUNT}:db:someone-elses-db",
    ],
)
def test_database_without_project_name_prefix_is_not_touched(arn: str) -> None:
    clients = make_clients(resources=[(arn, OWNED_TAGS)], state_arns=[arn])
    result = handler.run(budget_event(), make_cfg(), clients, NOW)
    assert_nothing_mutated(clients)
    assert result["refused"] and "prefix" in result["refused"][0]


def test_protected_database_is_not_touched() -> None:
    tags = {**OWNED_TAGS, "Lifecycle": "protected"}
    clients = make_clients(resources=[(DB_ARN, tags)], state_arns=[DB_ARN])
    result = handler.run(budget_event(), make_cfg(), clients, NOW)
    assert_nothing_mutated(clients)
    assert result["refused"] and "tag" in result["refused"][0]


def test_database_stop_failure_is_raised_after_the_other_shutdowns() -> None:
    clients = make_clients(resources=ALL_OWNED, state_arns=ALL_ARNS)
    clients.rds.stop_db_instance.side_effect = client_error("InvalidDBInstanceState")
    with pytest.raises(RuntimeError, match="InvalidDBInstanceState"):
        handler.run(budget_event(), make_cfg(), clients, NOW)
    clients.ecs.update_service.assert_called_once()
    clients.elbv2.delete_load_balancer.assert_called_once()


def test_database_restarted_by_aws_after_seven_days_is_stopped_again() -> None:
    # ECS at zero and the ALB already deleted - only the database runs again.
    clients = make_clients(
        resources=[(DB_ARN, OWNED_TAGS)], state_arns=[DB_ARN], created=NOW - timedelta(days=8)
    )
    result = handler.run({"trigger": "session-limit"}, make_cfg(), clients, NOW)
    assert result["status"] == "shut-down"
    clients.rds.stop_db_instance.assert_called_once_with(DBInstanceIdentifier="ekba-dev-postgres")
