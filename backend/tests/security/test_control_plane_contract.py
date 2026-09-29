"""The control plane returns registry data, never a company's content.

tenant-isolation.md section 2 allows exactly one cross-tenant surface and binds
it to "registry data only - company name, id, contact, seat counts", with no
function able to reach a document, a chunk, a conversation, a message, a
citation or a cached answer.

Subscription and metering rows were added to that surface so a platform
operator can see which plan a company is on. These tests pin the line: billing
facts about an account are allowed, anything describing what the account stores
is not - so the rule and the code cannot drift apart unnoticed.
"""

from __future__ import annotations

import inspect

from app.db import control_plane

# Models that hold a company's content. The control plane must not read them.
FORBIDDEN_MODELS = (
    "Document",
    "DocumentChunk",
    "Chunk",
    "Conversation",
    "Message",
    "Citation",
    "IngestionJob",
)


def test_the_control_plane_never_imports_a_content_model() -> None:
    imported = set(dir(control_plane))
    leaked = sorted(name for name in FORBIDDEN_MODELS if name in imported)
    assert not leaked, (
        f"control_plane imports content model(s) {leaked}; "
        "tenant-isolation.md section 2 forbids reaching tenant content from here"
    )


def test_no_control_plane_function_mentions_a_content_model() -> None:
    source = inspect.getsource(control_plane)
    # Names are checked as whole words so a comment quoting the rule is fine.
    for name in FORBIDDEN_MODELS:
        assert f"{name}." not in source, (
            f"control_plane reads {name}; registry data only - see tenant-isolation.md section 2"
        )


def test_billing_summary_exposes_account_facts_and_no_content() -> None:
    """The shape a platform operator receives, field by field."""
    from app.schemas import TenantBillingOut

    fields = set(TenantBillingOut.model_fields)
    assert fields == {
        "plan_id",
        "plan_name",
        "interval",
        "status",
        "complimentary",
        "cancel_at_period_end",
        "current_period_end",
        "base_amount_paise",
        "tokens_used",
        "committed_tokens",
        "reserved_tokens",
        "token_limit",
        "requests",
    }
    # Nothing that counts or names what the company stores.
    for banned in ("documents", "document_count", "conversations", "messages", "chunks"):
        assert banned not in fields


def test_the_registry_view_carries_no_payment_secret() -> None:
    """A Razorpay subscription or customer id is a payment reference, and the
    operator console has no use for one. Nothing secret reaches this schema."""
    from app.schemas import TenantBillingOut, TenantOut

    for model in (TenantBillingOut, TenantOut):
        for field in model.model_fields:
            lowered = field.lower()
            assert "secret" not in lowered
            assert "key" not in lowered or lowered.endswith("_key_id") is False
            assert "razorpay" not in lowered
            assert "password" not in lowered
            assert "token" not in lowered or lowered in {
                "tokens_used",
                "committed_tokens",
                "reserved_tokens",
                "token_limit",
            }


# --- the console must account for tokens exactly as the gate does -------------


def test_the_plan_limit_uses_the_quota_service_entitlement_key() -> None:
    """A rename of the entitlement must not leave the console reporting no
    limit at all, so the key is imported rather than written out again."""
    from app.services.llm_quota import ENTITLEMENT_KEY

    source = inspect.getsource(control_plane)
    assert "ENTITLEMENT_KEY" in source
    assert f'"{ENTITLEMENT_KEY}"' not in source, (
        "the entitlement key is hardcoded in control_plane; import it from "
        "app.services.llm_quota instead"
    )


def test_usage_counts_reserved_tokens_the_way_enforcement_does() -> None:
    """reservation_allowed() weighs committed + reserved + estimate, so a
    console that showed only committed would read under the allowance at the
    very moment the gate had started refusing."""
    from app.services.llm_quota import reservation_allowed

    committed, reserved, limit = 40_000, 9_000, 50_000
    # The gate refuses a 2k request at this point ...
    assert reservation_allowed(committed, reserved, 2_000, limit) is False
    # ... so the console must not be showing 40k of 50k, but 49k.
    reported = committed + reserved
    assert reported == 49_000
    assert reported / limit >= 0.8, "this company should read as near its quota"


def test_usage_rows_are_read_only() -> None:
    """The console reads the quota row; it never reserves, releases or writes.

    The docstring is stripped first: prose about "committed" tokens must not be
    mistaken for a commit() call.
    """
    func = control_plane.tenant_token_usage
    source = inspect.getsource(func).replace(func.__doc__ or "", "")
    for write in (
        "session.add(",
        "session.commit(",
        "session.flush(",
        "update(",
        "delete(",
        "insert(",
    ):
        assert write not in source, f"tenant_token_usage performs a write: {write}"
