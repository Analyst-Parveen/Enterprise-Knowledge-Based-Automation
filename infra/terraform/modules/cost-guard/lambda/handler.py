"""Cost guard: stop the ephemeral environment before it spends past the ceiling.

Invoked by:
  * SNS, when an AWS Budget crosses the shutdown threshold (gross usage, credits
    excluded) or any charge is not covered by credits (net usage).
  * EventBridge, hourly, to enforce the maximum demo session length.
  * An operator, directly, with {"trigger": "manual"} or {"trigger": "session-limit"}.

It acts on exactly three resource kinds, and only when a resource passes every
ownership signal in .claude/rules/terraform.md (name prefix, tags, presence in
the environment's Terraform state):

  * ECS services are scaled to zero tasks (not deleted).
  * Application load balancers are deleted - an ALB cannot be paused, and an
    idle one costs more per month than the whole project ceiling.
  * RDS instances are STOPPED, never deleted - the data stays. A stopped
    instance still bills its storage, and AWS starts it again after 7 days;
    the hourly check then finds it running past the session limit and stops
    it again.

Everything else - network, IAM, logs, and the entire protected baseline - is
left for scripts/destroy.sh, so Terraform stays the owner of the environment.
This function never runs terraform destroy and never deletes data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError

ECS_SERVICE_TYPE = "ecs:service"
LOAD_BALANCER_TYPE = "elasticloadbalancing:loadbalancer"
DB_INSTANCE_TYPE = "rds:db"

# RDS states in which the instance bills compute. "stopped"/"stopping" only
# bill storage, and "deleting" is on its way out.
DB_NOT_BILLABLE = frozenset({"stopped", "stopping", "deleting"})

LEFT_IN_PLACE = (
    "Left in place (no cost at rest; removed by scripts/destroy.sh): VPC, subnets, "
    "internet gateway, route tables, security groups, DB subnet group, target groups, "
    "ECS cluster, task definitions, IAM roles, log groups, alarms. A stopped RDS "
    "instance keeps its data and bills storage only (~$0.003/hour)."
)
PROTECTED = (
    "Never touched: secrets, ECR repositories and images, S3 buckets, Cognito, "
    "the Terraform state backend, budgets, and anything outside this project."
)


@dataclass(frozen=True)
class Config:
    dry_run: bool
    project_code: str
    environment: str
    state_bucket: str
    state_key: str
    trigger_topic_arn: str
    notify_topic_arn: str
    max_session_hours: float

    @property
    def name_prefix(self) -> str:
        return f"{self.project_code}-{self.environment}"

    @property
    def required_tags(self) -> dict[str, str]:
        return {
            "ProjectCode": self.project_code,
            "Environment": self.environment,
            "Lifecycle": "ephemeral",
        }

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            # Fail safe: anything other than an explicit "false" is a dry run.
            dry_run=os.environ.get("DRY_RUN", "true").strip().lower() != "false",
            project_code=os.environ["PROJECT_CODE"],
            environment=os.environ["ENVIRONMENT"],
            state_bucket=os.environ["STATE_BUCKET"],
            state_key=os.environ["STATE_KEY"],
            trigger_topic_arn=os.environ["TRIGGER_TOPIC_ARN"],
            notify_topic_arn=os.environ["NOTIFY_TOPIC_ARN"],
            max_session_hours=float(os.environ["MAX_SESSION_HOURS"]),
        )


@dataclass(frozen=True)
class Clients:
    tagging: Any
    ecs: Any
    elbv2: Any
    rds: Any
    s3: Any
    sns: Any

    @classmethod
    def create(cls) -> Clients:
        return cls(
            tagging=boto3.client("resourcegroupstaggingapi"),
            ecs=boto3.client("ecs"),
            elbv2=boto3.client("elbv2"),
            rds=boto3.client("rds"),
            s3=boto3.client("s3"),
            sns=boto3.client("sns"),
        )


@dataclass
class Resource:
    arn: str
    kind: str  # "ecs-service", "load-balancer" or "db-instance"
    label: str
    cluster: str = ""
    identifier: str = ""
    status: str = ""
    desired_count: int = 0
    created: datetime | None = None
    billable: bool = False


@dataclass
class Inventory:
    owned: list[Resource] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def billable(self) -> list[Resource]:
        return [r for r in self.owned if r.billable]


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"component": "cost-guard", "event": event, **fields}, default=str))


# ---------------------------------------------------------------------------
# Trigger classification
# ---------------------------------------------------------------------------
def classify(event: dict[str, Any], cfg: Config) -> tuple[str | None, str]:
    """Return (trigger, reason). trigger is None for events this function ignores."""
    records = event.get("Records")
    if isinstance(records, list) and records:
        sns = records[0].get("Sns", {})
        if (
            records[0].get("EventSource") != "aws:sns"
            or sns.get("TopicArn") != cfg.trigger_topic_arn
        ):
            return None, "SNS message from an unexpected topic"
        message = str(sns.get("Message", "")).strip()
        subject = sns.get("Subject") or (message.splitlines()[0] if message else "")
        return "budget", str(subject or "AWS Budget threshold crossed")

    trigger = event.get("trigger")
    if trigger == "session-limit":
        return "session-limit", "scheduled session-length check"
    if trigger == "manual":
        return "manual", "invoked manually by an operator"
    return None, "unrecognised event"


# ---------------------------------------------------------------------------
# Ownership - all three signals must agree, or the resource is not touched
# ---------------------------------------------------------------------------
def name_owned(name: str, prefix: str) -> bool:
    # "ekba-dev" or "ekba-dev-*". A look-alike such as "ekba-devx" is not ours.
    return name == prefix or name.startswith(prefix + "-")


def ownership_problems(
    arn: str, names: list[str], tags: dict[str, str], state_text: str, cfg: Config
) -> list[str]:
    problems = [
        f"name '{n}' lacks the {cfg.name_prefix} prefix"
        for n in names
        if not name_owned(n, cfg.name_prefix)
    ]
    problems += [
        f"tag {k}={tags.get(k)!r}, expected {v!r}"
        for k, v in cfg.required_tags.items()
        if tags.get(k) != v
    ]
    if f'"{arn}"' not in state_text:
        problems.append(f"not in Terraform state s3://{cfg.state_bucket}/{cfg.state_key}")
    return problems


def parse_names(arn: str) -> tuple[str, list[str]] | None:
    """Return (kind, names) for a supported ARN, or None."""
    resource = arn.split(":", 5)[-1]
    parts = resource.split("/")
    if parts[0] == "service" and len(parts) == 3:  # service/<cluster>/<service>
        return "ecs-service", [parts[1], parts[2]]
    if parts[:2] == ["loadbalancer", "app"] and len(parts) == 4:  # loadbalancer/app/<name>/<id>
        return "load-balancer", [parts[2]]
    if resource.startswith("db:") and len(parts) == 1:  # db:<identifier>
        return "db-instance", [resource[len("db:") :]]
    return None


def read_state(cfg: Config, clients: Clients) -> str:
    try:
        body = clients.s3.get_object(Bucket=cfg.state_bucket, Key=cfg.state_key)["Body"]
        return str(body.read().decode("utf-8"))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return ""  # environment never deployed or already destroyed: nothing is owned
        raise


def discover(cfg: Config, clients: Clients) -> list[tuple[str, dict[str, str]]]:
    paginator = clients.tagging.get_paginator("get_resources")
    found: list[tuple[str, dict[str, str]]] = []
    for page in paginator.paginate(
        TagFilters=[{"Key": k, "Values": [v]} for k, v in cfg.required_tags.items()],
        ResourceTypeFilters=[ECS_SERVICE_TYPE, LOAD_BALANCER_TYPE, DB_INSTANCE_TYPE],
    ):
        for mapping in page.get("ResourceTagMappingList", []):
            tags = {t["Key"]: t["Value"] for t in mapping.get("Tags", [])}
            found.append((mapping["ResourceARN"], tags))
    return found


def describe(arn: str, kind: str, names: list[str], clients: Clients) -> Resource | None:
    """Live state of one resource. None when it no longer exists."""
    if kind == "ecs-service":
        cluster, service = names
        out = clients.ecs.describe_services(cluster=cluster, services=[arn])
        if not out.get("services"):
            return None
        svc = out["services"][0]
        if svc.get("status") != "ACTIVE":
            return None
        running = (
            svc.get("desiredCount", 0) + svc.get("runningCount", 0) + svc.get("pendingCount", 0)
        )
        return Resource(
            arn=arn,
            kind=kind,
            label=f"ECS service {cluster}/{service}",
            cluster=cluster,
            desired_count=svc.get("desiredCount", 0),
            created=svc.get("createdAt"),
            billable=running > 0,
        )

    if kind == "db-instance":
        identifier = names[0]
        try:
            out = clients.rds.describe_db_instances(DBInstanceIdentifier=identifier)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "DBInstanceNotFound":
                return None
            raise
        db = out["DBInstances"][0]
        status = str(db.get("DBInstanceStatus", ""))
        return Resource(
            arn=arn,
            kind=kind,
            label=f"RDS instance {identifier}",
            identifier=identifier,
            status=status,
            created=db.get("InstanceCreateTime"),
            billable=status not in DB_NOT_BILLABLE,
        )

    try:
        out = clients.elbv2.describe_load_balancers(LoadBalancerArns=[arn])
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "LoadBalancerNotFound":
            return None
        raise
    lb = out["LoadBalancers"][0]
    return Resource(
        arn=arn,
        kind=kind,
        label=f"load balancer {names[0]}",
        created=lb.get("CreatedTime"),
        billable=True,  # an ALB bills every hour it exists
    )


def take_inventory(cfg: Config, clients: Clients) -> Inventory:
    inv = Inventory()
    try:
        state_text = read_state(cfg, clients)
    except ClientError as exc:
        # Ownership cannot be proven without the state, so nothing is touched.
        inv.errors.append(
            f"could not read Terraform state: {exc.response.get('Error', {}).get('Code')}"
        )
        return inv

    for arn, tags in discover(cfg, clients):
        parsed = parse_names(arn)
        if parsed is None:
            inv.refused.append(f"{arn}: unsupported resource type")
            continue
        kind, names = parsed
        problems = ownership_problems(arn, names, tags, state_text, cfg)
        if problems:
            inv.refused.append(f"{arn}: {'; '.join(problems)}")
            continue
        try:
            resource = describe(arn, kind, names, clients)
        except ClientError as exc:
            inv.errors.append(
                f"could not describe {arn}: {exc.response.get('Error', {}).get('Code')}"
            )
            continue
        if resource is not None:
            inv.owned.append(resource)
    return inv


# ---------------------------------------------------------------------------
# Shutdown - ECS first (stops compute and Bedrock calls, and the database's
# only client), then load balancers, then the database
# ---------------------------------------------------------------------------
SHUTDOWN_ORDER = {"ecs-service": 0, "load-balancer": 1, "db-instance": 2}


def shut_down(
    resources: list[Resource], cfg: Config, clients: Clients
) -> tuple[list[str], list[str]]:
    actions: list[str] = []
    errors: list[str] = []
    verb = "WOULD " if cfg.dry_run else ""

    for r in sorted(resources, key=lambda r: SHUTDOWN_ORDER[r.kind]):
        try:
            if r.kind == "ecs-service":
                if r.desired_count == 0:
                    actions.append(f"{r.label}: already at 0 tasks, draining")
                    continue
                if not cfg.dry_run:
                    clients.ecs.update_service(cluster=r.cluster, service=r.arn, desiredCount=0)
                actions.append(f"{verb}scale {r.label} from {r.desired_count} to 0 tasks")
            elif r.kind == "db-instance":
                if not cfg.dry_run:
                    clients.rds.stop_db_instance(DBInstanceIdentifier=r.identifier)
                actions.append(f"{verb}stop {r.label} (status {r.status}; data and storage kept)")
            else:
                if not cfg.dry_run:
                    clients.elbv2.delete_load_balancer(LoadBalancerArn=r.arn)
                actions.append(f"{verb}delete {r.label} (and its listeners)")
        except ClientError as exc:
            errors.append(f"{r.label}: {exc.response.get('Error', {}).get('Code')}")
    return actions, errors


def notify(cfg: Config, clients: Clients, subject: str, lines: list[str]) -> None:
    mode = "DRY RUN - nothing was changed" if cfg.dry_run else "LIVE"
    clients.sns.publish(
        TopicArn=cfg.notify_topic_arn,
        Subject=f"[{cfg.name_prefix} cost guard] {subject}"[:100],
        Message="\n".join([f"Mode: {mode}", *lines]),
    )


def run(
    event: dict[str, Any], cfg: Config, clients: Clients, now: datetime, request_id: str = "local"
) -> dict[str, Any]:
    trigger, reason = classify(event, cfg)
    if trigger is None:
        log("ignored", request_id=request_id, reason=reason)
        return {"status": "ignored", "reason": reason}

    inv = take_inventory(cfg, clients)
    billable = inv.billable

    if trigger == "session-limit":
        starts = [r.created for r in billable if r.created is not None]
        if not starts:
            log("no-billable-resources", request_id=request_id, trigger=trigger)
            return {"status": "nothing-running"}
        age_hours = (now - min(starts)).total_seconds() / 3600
        if age_hours < cfg.max_session_hours:
            log("within-session-limit", request_id=request_id, age_hours=round(age_hours, 2))
            return {"status": "within-session-limit", "age_hours": round(age_hours, 2)}
        reason = f"environment has existed {age_hours:.1f}h, limit is {cfg.max_session_hours:g}h"

    actions, errors = shut_down(billable, cfg, clients)
    errors = inv.errors + errors
    result = {
        "status": "dry-run" if cfg.dry_run else "shut-down",
        "trigger": trigger,
        "reason": reason,
        "actions": actions,
        "refused": inv.refused,
        "errors": errors,
    }
    log("shutdown", request_id=request_id, **result)

    # The hourly check stays quiet in dry-run; everything else is always reported.
    if not (trigger == "session-limit" and cfg.dry_run):
        lines = [
            f"Trigger: {trigger}",
            f"Reason: {reason}",
            f"Time (UTC): {now.isoformat(timespec='seconds')}",
            "",
            "Actions:",
            *([f"  - {a}" for a in actions] or ["  - nothing billable was running"]),
        ]
        if inv.refused:
            lines += ["", "NOT touched - failed ownership checks (manual review needed):"]
            lines += [f"  - {r}" for r in inv.refused]
        if errors:
            lines += ["", "ERRORS - manual action needed:", *[f"  - {e}" for e in errors]]
        lines += [
            "",
            LEFT_IN_PLACE,
            PROTECTED,
            "",
            "Next: ./scripts/destroy.sh, then ./scripts/deploy.sh when you want the demo back.",
        ]
        headline = (
            "shutdown FAILED" if errors else ("shutdown (dry run)" if cfg.dry_run else "shutdown")
        )
        notify(cfg, clients, headline, lines)

    if errors:
        # Fail the invocation so Lambda retries; every action above is idempotent.
        raise RuntimeError(f"cost guard finished with errors: {errors}")
    return result


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return run(
        event,
        Config.from_env(),
        Clients.create(),
        datetime.now(UTC),
        request_id=getattr(context, "aws_request_id", "local"),
    )
