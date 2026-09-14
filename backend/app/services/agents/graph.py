"""The LangGraph workflow graph.

    plan -> retrieve -> analyze -> synthesize -> cite -> END

Bounded by a step ceiling and a wall-clock timeout. A failing node degrades to a
clearly-labelled partial result rather than a silent empty answer.

See .claude/rules/ai-model-usage.md section 7.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from app.core.context import RequestContext, get_correlation_id
from app.core.logging import get_logger
from app.services.agents import nodes
from app.services.agents.state import AgentState, WorkflowType
from app.services.rag import prompts

logger = get_logger(__name__)

WORKFLOW_TIMEOUT_SECONDS = 120


@dataclass(slots=True)
class AgentResult:
    answer: str
    citations: list[dict[str, Any]]
    retrieved_chunks: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    workflow: str
    model_used: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    latency_ms: int
    tenant_id: str
    confidence: float
    partial: bool
    error: str | None
    correlation_id: str


def build_graph():  # type: ignore[no-untyped-def]
    """Compile the workflow graph. Linear and explicit - no unbounded loops."""
    graph = StateGraph(AgentState)

    graph.add_node("plan", nodes.plan_node)
    graph.add_node("retrieve", nodes.retrieve_node)
    graph.add_node("analyze", nodes.analyze_node)
    graph.add_node("synthesize", nodes.synthesize_node)
    graph.add_node("cite", nodes.cite_node)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "retrieve")

    # Skip analysis entirely when retrieval found nothing - no point paying for
    # a model call that has no sources to reason over.
    def after_retrieve(state: AgentState) -> str:
        return "synthesize" if not state.get("retrieved") else "analyze"

    graph.add_conditional_edges(
        "retrieve", after_retrieve, {"analyze": "analyze", "synthesize": "synthesize"}
    )
    graph.add_edge("analyze", "synthesize")
    graph.add_edge("synthesize", "cite")
    graph.add_edge("cite", END)

    return graph.compile()


_compiled = None


def get_graph():  # type: ignore[no-untyped-def]
    global _compiled
    if _compiled is None:
        _compiled = build_graph()
    return _compiled


async def run_workflow(
    ctx: RequestContext,
    *,
    workflow: WorkflowType,
    question: str,
    department: str | None = None,
    document_ids: list[str] | None = None,
) -> AgentResult:
    """Execute a workflow end to end, bounded by a timeout and step ceiling."""
    started = time.perf_counter()
    correlation_id = get_correlation_id()

    initial: AgentState = {
        "tenant_id": ctx.tenant_id,
        "user_id": ctx.user_id,
        "role": ctx.role,
        "correlation_id": correlation_id,
        "workflow": workflow.value,
        "question": question,
        "department": department,
        "document_ids": document_ids,
        "retrieved": [],
        "findings": [],
        "steps": [],
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": 0.0,
        "step_count": 0,
        "partial": False,
        "error": None,
    }

    partial = False
    error: str | None = None
    final: AgentState

    try:
        final = await asyncio.wait_for(
            get_graph().ainvoke(initial, config={"recursion_limit": nodes.MAX_STEPS}),
            timeout=WORKFLOW_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.error(
            "agent_workflow_timeout",
            extra={"extra": {"workflow": workflow.value, "timeout": WORKFLOW_TIMEOUT_SECONDS}},
        )
        partial, error = True, "timeout"
        final = dict(initial)  # type: ignore[assignment]
        final["answer"] = (
            "This workflow took too long and was stopped. Partial results only - "
            "try narrowing the question or selecting specific documents."
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the request
        logger.exception("agent_workflow_failed", extra={"extra": {"workflow": workflow.value}})
        partial, error = True, type(exc).__name__
        final = dict(initial)  # type: ignore[assignment]
        final["answer"] = "This workflow could not be completed. Partial results only."

    latency_ms = int((time.perf_counter() - started) * 1000)

    # Defence in depth: the tenant must be unchanged from entry to exit.
    if final.get("tenant_id") != ctx.tenant_id:
        from app.core.exceptions import TenantIsolationError
        from app.core.logging import log_security_event

        log_security_event(
            "tenant.agent_state_tenant_changed",
            reason="workflow_state_tenant_mismatch",
            severity="critical",
        )
        raise TenantIsolationError()

    return AgentResult(
        answer=final.get("answer") or prompts.NO_CONTEXT_ANSWER,
        citations=final.get("citations", []),
        retrieved_chunks=[
            {
                "chunk_id": r["payload"].get("chunk_id"),
                "document_id": r["payload"].get("document_id"),
                "document_name": r["payload"].get("document_name"),
                "page_number": r["payload"].get("page_number"),
                "section": r["payload"].get("section"),
                "score": round(float(r["score"]), 4),
                "preview": str(r["payload"].get("text", ""))[:280],
            }
            for r in final.get("retrieved", [])
        ],
        steps=final.get("steps", []),
        workflow=workflow.value,
        model_used=final.get("model_used", "none"),
        input_tokens=final.get("input_tokens", 0),
        output_tokens=final.get("output_tokens", 0),
        estimated_cost=round(final.get("estimated_cost", 0.0), 8),
        latency_ms=latency_ms,
        tenant_id=ctx.tenant_id,
        confidence=final.get("confidence", 0.0),
        partial=partial,
        error=error,
        correlation_id=correlation_id,
    )
