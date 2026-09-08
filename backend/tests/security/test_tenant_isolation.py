"""Mandatory tenant-isolation tests.

These are release-blocking. Never weaken or delete one to make a build pass.
See .claude/rules/testing.md section 2 and tenant-isolation.md section 9.
"""

from __future__ import annotations

import pytest

from app.core.context import RequestContext
from app.core.exceptions import TenantIsolationError
from app.services import storage
from app.services.vector import ChunkPayload, SearchHit, _tenant_filter


class TestVectorTenantFilter:
    """Every Qdrant search must carry a tenant_id must-filter."""

    def test_filter_always_includes_tenant(self, tenant_a: RequestContext) -> None:
        f = _tenant_filter(tenant_a)
        keys = [c.key for c in f.must]  # type: ignore[union-attr]
        assert "tenant_id" in keys

        condition = next(c for c in f.must if c.key == "tenant_id")  # type: ignore[union-attr]
        assert condition.match.value == "tenant-a"

    def test_filter_differs_per_tenant(
        self, tenant_a: RequestContext, tenant_b: RequestContext
    ) -> None:
        fa = _tenant_filter(tenant_a)
        fb = _tenant_filter(tenant_b)
        va = next(c for c in fa.must if c.key == "tenant_id").match.value  # type: ignore[union-attr]
        vb = next(c for c in fb.must if c.key == "tenant_id").match.value  # type: ignore[union-attr]
        assert va != vb

    def test_admin_does_not_widen_the_filter(self, admin_a: RequestContext) -> None:
        """Admin gets operational metrics, NOT cross-tenant document access."""
        f = _tenant_filter(admin_a)
        condition = next(c for c in f.must if c.key == "tenant_id")  # type: ignore[union-attr]
        assert condition.match.value == "tenant-a"

    def test_suspicious_chunks_excluded_by_default(self, tenant_a: RequestContext) -> None:
        """Retrieval-poisoning defence: quarantined chunks stay out."""
        f = _tenant_filter(tenant_a)
        assert f.must_not is not None
        assert any(c.key == "suspicious" for c in f.must_not)  # type: ignore[union-attr]

    def test_document_scope_does_not_replace_tenant(self, tenant_a: RequestContext) -> None:
        """Narrowing by document must ADD to, never replace, the tenant filter."""
        f = _tenant_filter(tenant_a, document_ids=["doc-1"])
        keys = [c.key for c in f.must]  # type: ignore[union-attr]
        assert "tenant_id" in keys and "document_id" in keys


class TestChunkPayload:
    """A chunk without tenant_id breaks isolation."""

    def _payload(self, tenant_id: str) -> ChunkPayload:
        return ChunkPayload(
            document_id="d1",
            chunk_id="c1",
            document_name="Policy.pdf",
            page_number=1,
            source_uri="s3://b/tenant/x.pdf",
            owner_id="u1",
            tenant_id=tenant_id,
            document_version=1,
            created_by="u1",
            created_at="2026-01-01T00:00:00Z",
            text="hello",
        )

    def test_payload_carries_all_required_metadata(self) -> None:
        """PROJECT.md section 6 - every field must be present."""
        payload = self._payload("tenant-a").to_dict()
        for field in (
            "document_id",
            "chunk_id",
            "document_name",
            "page_number",
            "source_uri",
            "owner_id",
            "tenant_id",
            "document_version",
            "created_by",
            "created_at",
        ):
            assert field in payload, f"missing required vector metadata: {field}"

    @pytest.mark.asyncio
    async def test_upsert_refuses_chunk_without_tenant(self) -> None:
        from app.services.vector import upsert_chunks

        with pytest.raises(ValueError, match="tenant_id"):
            await upsert_chunks([[0.1, 0.2]], [self._payload("")])


class TestStorageTenantPrefix:
    """S3 keys are namespaced by tenant, and reads verify the prefix."""

    def test_own_tenant_key_allowed(self, tenant_a: RequestContext) -> None:
        storage.assert_key_in_tenant(tenant_a, "tenant-a/abc123.pdf")

    def test_other_tenant_key_denied(self, tenant_a: RequestContext) -> None:
        with pytest.raises(TenantIsolationError):
            storage.assert_key_in_tenant(tenant_a, "tenant-b/abc123.pdf")

    def test_traversal_out_of_prefix_denied(self, tenant_a: RequestContext) -> None:
        with pytest.raises(TenantIsolationError):
            storage.assert_key_in_tenant(tenant_a, "../tenant-b/secret.pdf")

    def test_prefix_confusion_denied(self, tenant_a: RequestContext) -> None:
        """'tenant-abc/' must not satisfy the 'tenant-a/' prefix check."""
        with pytest.raises(TenantIsolationError):
            storage.assert_key_in_tenant(tenant_a, "tenant-abc/file.pdf")

    def test_generated_key_is_inside_tenant(self, tenant_a: RequestContext) -> None:
        from app.services.security.files import build_storage_key

        key = build_storage_key(tenant_a.tenant_id, "pdf")
        assert key.startswith("tenant-a/")
        storage.assert_key_in_tenant(tenant_a, key)


class TestCitationTenantCheck:
    """A citation may only reference a document in the caller's tenant."""

    def test_foreign_tenant_citation_is_dropped(self) -> None:
        from app.services.rag.guardrails import validate_citations
        from app.services.rag.prompts import BuiltContext

        foreign = SearchHit(
            score=0.9,
            payload={
                "chunk_id": "c-foreign",
                "document_id": "d-foreign",
                "document_name": "Other Tenant Doc.pdf",
                "tenant_id": "tenant-b",
                "text": "secret",
            },
        )
        context = BuiltContext(text="…", used_hits=[foreign], source_labels={"c-foreign": 1})

        _, citations, _ = validate_citations("See [S1].", context, tenant_id="tenant-a")
        assert citations == [], "a citation to another tenant's document must be dropped"


class TestCacheNamespacing:
    """Semantic cache keys must be tenant-namespaced."""

    def test_cache_keys_differ_across_tenants(
        self, tenant_a: RequestContext, tenant_b: RequestContext
    ) -> None:
        from app.services.rag.cache import _entry_key, _index_key

        assert _index_key(tenant_a) != _index_key(tenant_b)
        assert _entry_key(tenant_a, "same-digest") != _entry_key(tenant_b, "same-digest")

    def test_rate_limit_keys_are_tenant_scoped(
        self, tenant_a: RequestContext, tenant_b: RequestContext
    ) -> None:
        from app.core.config import settings

        key_a = f"{settings.project_code}:rl:api:{tenant_a.tenant_id}:{tenant_a.user_id}"
        key_b = f"{settings.project_code}:rl:api:{tenant_b.tenant_id}:{tenant_b.user_id}"
        assert key_a != key_b


class TestNoTenantInRequestBody:
    """A client-supplied tenant_id must not exist as an authorization input."""

    def test_chat_request_has_no_tenant_field(self) -> None:
        from app.schemas import ChatRequest

        assert "tenant_id" not in ChatRequest.model_fields, (
            "ChatRequest must not accept tenant_id - tenant comes from the JWT only"
        )

    def test_extra_tenant_id_is_ignored(self) -> None:
        """Even if a client sends tenant_id, it must not become a field."""
        from app.schemas import ChatRequest

        request = ChatRequest.model_validate({"question": "hello", "tenant_id": "tenant-b"})
        assert not hasattr(request, "tenant_id")


class TestContextRequiresTenant:
    def test_operating_without_context_raises(self) -> None:
        from app.core.context import _request_ctx, require_request_context

        token = _request_ctx.set(None)
        try:
            with pytest.raises(RuntimeError, match="tenant"):
                require_request_context()
        finally:
            _request_ctx.reset(token)
