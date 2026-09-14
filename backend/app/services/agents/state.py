"""LangGraph agent state.

The tenant context is threaded through state from the entry node onward. Every
node that touches data reads the tenant from here - a node that queries without
it is a bug, not an optimization.

See .claude/rules/ai-model-usage.md section 7.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, TypedDict


class WorkflowType(str, Enum):
    POLICY_COMPARISON = "policy_comparison"
    SUMMARIZATION = "summarization"
    CROSS_DOCUMENT_ANALYSIS = "cross_document_analysis"
    KNOWLEDGE_EXTRACTION = "knowledge_extraction"
    REPORT_GENERATION = "report_generation"


def _append(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """Reducer: nodes append to shared lists rather than overwriting them."""
    return (left or []) + (right or [])


def _add(left: int | float | None, right: int | float | None) -> int | float:
    return (left or 0) + (right or 0)


class AgentState(TypedDict, total=False):
    # -- tenant context: set once at entry, never mutated by a node ------
    tenant_id: str
    user_id: str
    role: str
    correlation_id: str

    # -- request ---------------------------------------------------------
    workflow: str
    question: str
    department: str | None
    document_ids: list[str] | None

    # -- working data ----------------------------------------------------
    retrieved: Annotated[list[dict[str, Any]], _append]
    findings: Annotated[list[dict[str, Any]], _append]
    steps: Annotated[list[dict[str, Any]], _append]

    # -- output ----------------------------------------------------------
    answer: str
    citations: list[dict[str, Any]]
    confidence: float

    # -- accounting ------------------------------------------------------
    input_tokens: Annotated[int, _add]
    output_tokens: Annotated[int, _add]
    estimated_cost: Annotated[float, _add]
    model_used: str

    # -- control ---------------------------------------------------------
    step_count: Annotated[int, _add]
    partial: bool
    error: str | None
