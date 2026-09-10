"""LangGraph nodes.

Every node that touches data rebuilds a RequestContext from the tenant fields in
state and passes it to the retrieval layer, which applies the tenant filter
internally. No node can query across tenants.
"""

from __future__ import annotations

from typing import Any, cast

from app.core.config import settings
from app.core.context import RequestContext, Role
from app.core.logging import get_logger
from app.services import vector
from app.services.agents.state import AgentState, WorkflowType
from app.services.ai.provider import get_provider
from app.services.rag import guardrails, prompts
from app.services.rag.rerank import rerank
from app.services.vector import SearchHit

logger = get_logger(__name__)

MAX_STEPS = 12  # hard bound - no unbounded loops


def ctx_from_state(state: AgentState) -> RequestContext:
    """Rebuild the tenant context. This is the only identity a node may use."""
    return RequestContext(
        user_id=state["user_id"],
        tenant_id=state["tenant_id"],
        role=cast(Role, state.get("role", "user")),
        correlation_id=state.get("correlation_id", ""),
    )


def _hit_to_dict(hit: SearchHit) -> dict[str, Any]:
    return {"score": hit.score, "payload": hit.payload}


def _dict_to_hit(raw: dict[str, Any]) -> SearchHit:
    return SearchHit(score=float(raw["score"]), payload=raw["payload"])


# ---------------------------------------------------------------------------
# 1. plan
# ---------------------------------------------------------------------------
async def plan_node(state: AgentState) -> dict[str, Any]:
    """Turn the request into the retrieval queries this workflow needs."""
    workflow = state.get("workflow", WorkflowType.SUMMARIZATION.value)
    question = state.get("question", "")

    queries: list[str]
    if workflow == WorkflowType.POLICY_COMPARISON.value:
        # Two sweeps so both the old and the new document surface.
        queries = [question, f"previous superseded earlier version {question}"]
    elif workflow == WorkflowType.CROSS_DOCUMENT_ANALYSIS.value:
        queries = [question, f"related context background {question}"]
    else:
        queries = [question]

    return {
        "steps": [{"node": "plan", "workflow": workflow, "queries": len(queries)}],
        "step_count": 1,
        "findings": [{"kind": "queries", "value": queries}],
    }


# ---------------------------------------------------------------------------
# 2. find documents
# ---------------------------------------------------------------------------
async def retrieve_node(state: AgentState) -> dict[str, Any]:
    """Tenant-filtered retrieval. The filter is built inside vector.search."""
    ctx = ctx_from_state(state)
    provider = get_provider()

    queries = [state.get("question", "")]
    for finding in state.get("findings", []):
        if finding.get("kind") == "queries":
            queries = list(finding["value"])
            break

    embed_result = await provider.embed(queries)

    seen: set[str] = set()
    collected: list[SearchHit] = []
    for vec in embed_result.vectors:
        hits = await vector.search(
            ctx,
            vec,
            top_k=settings.retrieval_top_k,
            document_ids=state.get("document_ids"),
            department=state.get("department"),
        )
        for hit in hits:
            chunk_id = str(hit.payload.get("chunk_id"))
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            collected.append(hit)

    collected = [h for h in collected if h.score >= settings.effective_relevance_threshold]
    collected = rerank(state.get("question", ""), collected, top_k=settings.retrieval_top_k)

    return {
        "retrieved": [_hit_to_dict(h) for h in collected],
        "steps": [{"node": "retrieve", "hits": len(collected)}],
        "step_count": 1,
        "input_tokens": embed_result.input_tokens,
        "estimated_cost": embed_result.estimated_cost,
    }


# ---------------------------------------------------------------------------
# 3. analyze
# ---------------------------------------------------------------------------
_ANALYSIS_INSTRUCTIONS = {
    WorkflowType.POLICY_COMPARISON.value: (
        "Group the sources by which document version they come from. For each "
        "group, list the concrete rules, limits, amounts and deadlines it states. "
        "Do not compare yet - only extract, with the source marker for each item."
    ),
    WorkflowType.SUMMARIZATION.value: (
        "List the key points across the sources, each with its source marker. "
        "Preserve exact figures, dates and policy wording."
    ),
    WorkflowType.CROSS_DOCUMENT_ANALYSIS.value: (
        "Identify the themes that appear across more than one document, and note "
        "any place where two documents disagree. Cite every item."
    ),
    WorkflowType.KNOWLEDGE_EXTRACTION.value: (
        "Extract structured facts as 'field: value' lines, each with its source "
        "marker. Extract only what is stated - never infer."
    ),
    WorkflowType.REPORT_GENERATION.value: (
        "Extract the findings that belong in a report: figures, trends, risks and "
        "recommendations that the sources actually state. Cite every item."
    ),
}


async def analyze_node(state: AgentState) -> dict[str, Any]:
    """Extract structured findings from retrieved sources - no conclusions yet."""
    retrieved = state.get("retrieved", [])
    if not retrieved:
        return {
            "steps": [{"node": "analyze", "skipped": "no_sources"}],
            "step_count": 1,
        }

    hits = [_dict_to_hit(r) for r in retrieved]
    context = prompts.build_context(hits, max_chars=settings.max_input_tokens * 3)
    workflow = state.get("workflow", WorkflowType.SUMMARIZATION.value)
    instruction = _ANALYSIS_INSTRUCTIONS.get(
        workflow, _ANALYSIS_INSTRUCTIONS[WorkflowType.SUMMARIZATION.value]
    )

    result = await get_provider().chat(
        system=prompts.SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            f"{context.text}\n\n"
                            f"TASK: {instruction}\n\n"
                            f"USER REQUEST: {state.get('question', '')}"
                        )
                    }
                ],
            }
        ],
        max_tokens=settings.max_output_tokens,
        temperature=0.0,
    )

    return {
        "findings": [{"kind": "analysis", "value": result.text}],
        "steps": [{"node": "analyze", "chars": len(result.text)}],
        "step_count": 1,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "estimated_cost": result.estimated_cost,
        "model_used": result.model_used,
    }


# ---------------------------------------------------------------------------
# 4. synthesize
# ---------------------------------------------------------------------------
_SYNTHESIS_INSTRUCTIONS = {
    WorkflowType.POLICY_COMPARISON.value: (
        "Write a comparison. State what changed, what stayed the same, and what "
        "was removed. Use a short table where it helps. Cite every claim. If only "
        "one version was found, say so explicitly rather than inventing the other."
    ),
    WorkflowType.SUMMARIZATION.value: (
        "Write a concise summary organised by theme. Cite every claim."
    ),
    WorkflowType.CROSS_DOCUMENT_ANALYSIS.value: (
        "Write the analysis: shared themes first, then any contradictions between "
        "documents, naming both sides. Cite every claim."
    ),
    WorkflowType.KNOWLEDGE_EXTRACTION.value: (
        "Present the extracted facts as a clean 'field: value' list. Cite each."
    ),
    WorkflowType.REPORT_GENERATION.value: (
        "Write a short report: Summary, Findings, Risks, Recommendations. Cite "
        "every factual claim. Mark any recommendation that is your inference "
        "rather than something the sources state."
    ),
}


async def synthesize_node(state: AgentState) -> dict[str, Any]:
    """Produce the final answer from the findings."""
    retrieved = state.get("retrieved", [])
    if not retrieved:
        return {
            "answer": prompts.NO_CONTEXT_ANSWER,
            "confidence": 0.0,
            "citations": [],
            "steps": [{"node": "synthesize", "skipped": "no_sources"}],
            "step_count": 1,
            "model_used": state.get("model_used", "none"),
        }

    analysis = ""
    for finding in state.get("findings", []):
        if finding.get("kind") == "analysis":
            analysis = str(finding["value"])
            break

    hits = [_dict_to_hit(r) for r in retrieved]
    context = prompts.build_context(hits, max_chars=settings.max_input_tokens * 3)
    workflow = state.get("workflow", WorkflowType.SUMMARIZATION.value)
    instruction = _SYNTHESIS_INSTRUCTIONS.get(
        workflow, _SYNTHESIS_INSTRUCTIONS[WorkflowType.SUMMARIZATION.value]
    )

    result = await get_provider().chat(
        system=prompts.SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "text": (
                            f"{context.text}\n\n"
                            f"INTERMEDIATE ANALYSIS (yours, from the sources above):\n"
                            f"{analysis}\n\n"
                            f"TASK: {instruction}\n\n"
                            f"USER REQUEST: {state.get('question', '')}"
                        )
                    }
                ],
            }
        ],
        max_tokens=settings.max_output_tokens,
        temperature=0.1,
    )

    return {
        "answer": result.text,
        "steps": [{"node": "synthesize", "chars": len(result.text)}],
        "step_count": 1,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "estimated_cost": result.estimated_cost,
        "model_used": result.model_used,
    }


# ---------------------------------------------------------------------------
# 5. cite  (citation validation + output guardrail, same as the RAG pipeline)
# ---------------------------------------------------------------------------
async def cite_node(state: AgentState) -> dict[str, Any]:
    retrieved = state.get("retrieved", [])
    answer = state.get("answer", "")

    if not retrieved or not answer:
        return {
            "citations": [],
            "confidence": 0.0,
            "steps": [{"node": "cite", "skipped": "nothing_to_validate"}],
            "step_count": 1,
        }

    hits = [_dict_to_hit(r) for r in retrieved]
    context = prompts.build_context(hits, max_chars=settings.max_input_tokens * 3)

    cleaned, citations, invented = guardrails.validate_citations(
        answer, context, state["tenant_id"]
    )
    guarded, block_reason = guardrails.apply_output_guardrail(cleaned)
    confidence = guardrails.compute_confidence(hits, citations, invented, no_context=False)

    from dataclasses import asdict

    return {
        "answer": guarded,
        "citations": [] if block_reason else [asdict(c) for c in citations],
        "confidence": 0.0 if block_reason else confidence,
        "steps": [
            {
                "node": "cite",
                "citations": len(citations),
                "invented": invented,
                "blocked": block_reason,
            }
        ],
        "step_count": 1,
    }
