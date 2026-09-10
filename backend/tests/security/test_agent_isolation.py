"""Agent workflows must stay inside one tenant at every node.

Mandatory security test #5 from .claude/rules/testing.md section 2.
"""

from __future__ import annotations

import pytest

from app.core.context import RequestContext
from app.services import vector
from app.services.agents import graph, nodes
from app.services.agents.state import WorkflowType
from app.services.ai.provider import LocalProvider
from app.services.vector import SearchHit


def hit(tenant: str, chunk_id: str, text: str) -> SearchHit:
    return SearchHit(
        score=0.8,
        payload={
            "chunk_id": chunk_id,
            "document_id": f"doc-{tenant}",
            "document_name": f"Policy ({tenant}).pdf",
            "page_number": 1,
            "tenant_id": tenant,
            "text": text,
        },
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Retrieval honours the tenant boundary, as the real implementation does."""
    corpus = [
        hit("tenant-a", "a1", "Tenant A travel limit is 150 USD per night."),
        hit("tenant-b", "b1", "Tenant B travel limit is 999 USD per night."),
    ]
    seen_tenants: list[str] = []

    async def fake_search(ctx, query_vector, **kwargs):  # type: ignore[no-untyped-def]
        seen_tenants.append(ctx.tenant_id)
        return [h for h in corpus if h.tenant_id == ctx.tenant_id]

    monkeypatch.setattr(vector, "search", fake_search)
    monkeypatch.setattr(nodes, "get_provider", lambda: LocalProvider())
    return {"seen_tenants": seen_tenants}


class TestAgentTenantIsolation:
    @pytest.mark.asyncio
    async def test_workflow_only_sees_own_tenant(
        self, tenant_a: RequestContext, wired: dict
    ) -> None:
        result = await graph.run_workflow(
            tenant_a,
            workflow=WorkflowType.SUMMARIZATION,
            question="What is the travel limit?",
        )
        assert result.tenant_id == "tenant-a"
        for chunk in result.retrieved_chunks:
            assert chunk["document_id"] == "doc-tenant-a"
        assert "999" not in result.answer, "tenant B's figure leaked into tenant A's answer"

    @pytest.mark.asyncio
    async def test_every_node_queries_with_the_caller_tenant(
        self, tenant_b: RequestContext, wired: dict
    ) -> None:
        await graph.run_workflow(
            tenant_b,
            workflow=WorkflowType.CROSS_DOCUMENT_ANALYSIS,
            question="What is the travel limit?",
        )
        assert wired["seen_tenants"], "retrieval was never called"
        assert set(wired["seen_tenants"]) == {"tenant-b"}, (
            "a node queried with a tenant other than the caller's"
        )

    @pytest.mark.asyncio
    async def test_state_carries_tenant_from_entry(self, tenant_a: RequestContext) -> None:
        state = {"tenant_id": "tenant-a", "user_id": "user-a", "role": "user"}
        ctx = nodes.ctx_from_state(state)  # type: ignore[arg-type]
        assert ctx.tenant_id == "tenant-a"

    @pytest.mark.asyncio
    async def test_citations_stay_within_tenant(
        self, tenant_a: RequestContext, wired: dict
    ) -> None:
        result = await graph.run_workflow(
            tenant_a,
            workflow=WorkflowType.POLICY_COMPARISON,
            question="Compare the travel limits.",
        )
        for citation in result.citations:
            assert citation["document_id"] == "doc-tenant-a"


class TestAgentBounds:
    @pytest.mark.asyncio
    async def test_no_sources_degrades_honestly(
        self, tenant_a: RequestContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def empty_search(ctx, query_vector, **kwargs):  # type: ignore[no-untyped-def]
            return []

        monkeypatch.setattr(vector, "search", empty_search)
        monkeypatch.setattr(nodes, "get_provider", lambda: LocalProvider())

        result = await graph.run_workflow(
            tenant_a, workflow=WorkflowType.SUMMARIZATION, question="Anything?"
        )
        assert result.citations == []
        assert result.confidence == 0.0
        assert "could not find" in result.answer.lower()

    @pytest.mark.asyncio
    async def test_node_failure_degrades_to_partial(
        self, tenant_a: RequestContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing node yields a labelled partial result, never a crash."""

        async def exploding_search(ctx, query_vector, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("vector store is down")

        monkeypatch.setattr(vector, "search", exploding_search)
        monkeypatch.setattr(nodes, "get_provider", lambda: LocalProvider())

        result = await graph.run_workflow(
            tenant_a, workflow=WorkflowType.SUMMARIZATION, question="Anything?"
        )
        assert result.partial is True
        assert result.error == "RuntimeError"
        assert result.answer

    @pytest.mark.asyncio
    async def test_usage_is_accumulated(self, tenant_a: RequestContext, wired: dict) -> None:
        result = await graph.run_workflow(
            tenant_a, workflow=WorkflowType.SUMMARIZATION, question="travel limit?"
        )
        assert result.input_tokens > 0
        assert result.latency_ms >= 0
        assert result.steps, "the workflow trace must be reported"

    def test_step_ceiling_is_bounded(self) -> None:
        assert nodes.MAX_STEPS <= 20
        assert graph.WORKFLOW_TIMEOUT_SECONDS <= 300


class TestWorkflowCoverage:
    def test_all_project_workflows_exist(self) -> None:
        """PROJECT.md section 5 lists these workflow families."""
        values = {w.value for w in WorkflowType}
        assert values == {
            "policy_comparison",
            "summarization",
            "cross_document_analysis",
            "knowledge_extraction",
            "report_generation",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("workflow", list(WorkflowType))
    async def test_every_workflow_runs(
        self, workflow: WorkflowType, tenant_a: RequestContext, wired: dict
    ) -> None:
        result = await graph.run_workflow(
            tenant_a, workflow=workflow, question="What is the travel limit?"
        )
        assert result.workflow == workflow.value
        assert result.answer
        assert result.partial is False
