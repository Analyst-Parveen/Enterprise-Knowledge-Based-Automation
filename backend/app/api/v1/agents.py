"""Agentic automation endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, ServerUser
from app.core.config import settings
from app.core.exceptions import GuardrailError, ValidationError
from app.db import repositories as repo
from app.schemas import AgentRequest, AgentResponseOut, WorkflowInfo
from app.services.agents.graph import run_workflow
from app.services.agents.state import WorkflowType
from app.services.rag import prompts
from app.services.security.injection import scan_input, validate_question

router = APIRouter(prefix="/agents", tags=["agents"])

_DESCRIPTIONS = {
    WorkflowType.POLICY_COMPARISON: ("Compare two versions of a policy and report what changed.",),
    WorkflowType.SUMMARIZATION: ("Summarize one or more documents by theme.",),
    WorkflowType.CROSS_DOCUMENT_ANALYSIS: (
        "Find shared themes and contradictions across documents.",
    ),
    WorkflowType.KNOWLEDGE_EXTRACTION: ("Extract structured facts as field/value pairs.",),
    WorkflowType.REPORT_GENERATION: ("Produce a summary/findings/risks/recommendations report.",),
}


@router.get("/workflows", response_model=list[WorkflowInfo])
async def list_workflows(ctx: ServerUser) -> list[WorkflowInfo]:
    return [
        WorkflowInfo(
            id=workflow.value,
            name=workflow.value.replace("_", " ").title(),
            description=description[0],
        )
        for workflow, description in _DESCRIPTIONS.items()
    ]


@router.post("/run", response_model=AgentResponseOut)
async def run_agent(payload: AgentRequest, ctx: ServerUser, session: DbSession) -> AgentResponseOut:
    """Run an agentic workflow.

    Uses the stricter server-side rate limit (10/min) because a workflow makes
    several model calls per request.
    """
    spent_today = await repo.daily_spend(session, ctx)
    if spent_today >= settings.daily_cost_ceiling_usd:
        raise ValidationError("You have reached today's AI usage limit for this account.")

    question = validate_question(payload.question)
    scan = scan_input(question)
    if scan.is_blocking:
        await repo.record_audit(
            session,
            event_type="guardrail.input_blocked",
            ctx=ctx,
            severity="error",
            reason=",".join(scan.reasons),
        )
        await session.commit()
        raise GuardrailError(prompts.BLOCKED_ANSWER)

    result = await run_workflow(
        ctx,
        workflow=payload.workflow,
        question=question,
        department=payload.department.value if payload.department else None,
        document_ids=payload.document_ids,
    )

    await repo.record_usage(
        session,
        ctx,
        operation=f"agent.{result.workflow}",
        model_used=result.model_used,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost=result.estimated_cost,
        latency_ms=result.latency_ms,
        success=not result.partial,
    )
    await session.commit()

    return AgentResponseOut(
        answer=result.answer,
        citations=result.citations,
        retrieved_chunks=result.retrieved_chunks,
        steps=result.steps,
        workflow=result.workflow,
        model_used=result.model_used,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost=result.estimated_cost,
        latency_ms=result.latency_ms,
        tenant_id=result.tenant_id,
        confidence=result.confidence,
        partial=result.partial,
        error=result.error,
        correlation_id=result.correlation_id,
    )
