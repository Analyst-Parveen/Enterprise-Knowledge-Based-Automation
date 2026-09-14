"""The RAG pipeline - PROJECT.md section 4, in order, no stage skipped.

    1  Authentication          (upstream, in the route dependency)
    2  Input validation
    3  Prompt injection scan
    4  Semantic cache
    5  Tenant / permission filtering
    6  Retrieval
    7  Relevance threshold
    8  Context construction
    9  Model routing
    10 Reranking
    11 Citation validation
    12 Output guardrail
    13 Token / cost tracking
    14 Language handling
    -> Response

A cache hit (stage 4) still runs tenant filtering and the output guardrail.
See .claude/rules/guardrails.md section 7.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.context import RequestContext, get_correlation_id
from app.core.exceptions import GuardrailError
from app.core.logging import get_logger
from app.db import repositories as repo
from app.services import vector
from app.services.ai.provider import get_provider
from app.services.rag import cache, guardrails, prompts
from app.services.rag.rerank import rerank
from app.services.security.injection import scan_input, validate_question

logger = get_logger(__name__)


@dataclass(slots=True)
class ChatResponse:
    """The response contract from PROJECT.md section 7."""

    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    latency_ms: int = 0
    cache_hit: bool = False
    tenant_id: str = ""
    confidence: float = 0.0
    correlation_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _chunk_summary(hit: vector.SearchHit) -> dict[str, Any]:
    """What we expose about a retrieved chunk - never the whole document."""
    payload = hit.payload
    text = hit.text
    return {
        "chunk_id": payload.get("chunk_id"),
        "document_id": payload.get("document_id"),
        "document_name": payload.get("document_name"),
        "page_number": payload.get("page_number"),
        "section": payload.get("section"),
        "modality": payload.get("modality"),
        "score": round(hit.score, 4),
        "preview": text[:280] + ("…" if len(text) > 280 else ""),
    }


async def answer_question(
    session: AsyncSession,
    ctx: RequestContext,
    question: str,
    *,
    department: str | None = None,
    document_ids: list[str] | None = None,
) -> ChatResponse:
    started = time.perf_counter()
    provider = get_provider()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    # -- 2. input validation ---------------------------------------------
    question = validate_question(question)

    # -- 3. prompt injection scan ----------------------------------------
    scan = scan_input(question)
    if scan.is_blocking:
        await repo.record_audit(
            session,
            event_type="guardrail.input_blocked",
            ctx=ctx,
            severity="error",
            reason=",".join(scan.reasons),
        )
        await repo.record_usage(
            session,
            ctx,
            operation="chat",
            model_used=None,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0.0,
            latency_ms=elapsed_ms(),
            success=False,
        )
        # Still a full envelope, so observability stays intact.
        raise GuardrailError(prompts.BLOCKED_ANSWER)

    # Embed the question once - used for both cache lookup and retrieval.
    embed_result = await provider.embed([question])
    query_vector = embed_result.vectors[0]

    # -- 4. semantic cache (tenant-namespaced) ---------------------------
    cached = await cache.lookup(ctx, question, query_vector)
    if cached is not None:
        # A cache hit does NOT bypass the output guardrail.
        guarded_answer, block_reason = guardrails.apply_output_guardrail(cached.answer)
        latency = elapsed_ms()

        await repo.record_usage(
            session,
            ctx,
            operation="chat",
            model_used=cached.model_used,
            input_tokens=embed_result.input_tokens,
            output_tokens=0,
            estimated_cost=embed_result.estimated_cost,
            latency_ms=latency,
            cache_hit=True,
        )
        return ChatResponse(
            answer=guarded_answer,
            citations=cached.citations if not block_reason else [],
            retrieved_chunks=[],
            model_used=cached.model_used,
            input_tokens=embed_result.input_tokens,
            output_tokens=0,
            estimated_cost=embed_result.estimated_cost,
            latency_ms=latency,
            cache_hit=True,
            tenant_id=ctx.tenant_id,
            confidence=cached.confidence,
            correlation_id=get_correlation_id(),
        )

    # -- 5 & 6. tenant-filtered retrieval --------------------------------
    # The tenant filter is built inside vector.search - it cannot be omitted.
    hits = await vector.search(
        ctx,
        query_vector,
        top_k=settings.retrieval_top_k * 2,  # over-fetch, rerank narrows it
        document_ids=document_ids,
        department=department,
    )

    # -- 7. relevance threshold ------------------------------------------
    hits = [h for h in hits if h.score >= settings.effective_relevance_threshold]

    if not hits:
        latency = elapsed_ms()
        await repo.record_usage(
            session,
            ctx,
            operation="chat",
            model_used=None,
            input_tokens=embed_result.input_tokens,
            output_tokens=0,
            estimated_cost=embed_result.estimated_cost,
            latency_ms=latency,
        )
        # Say we don't know rather than inventing an answer.
        return ChatResponse(
            answer=prompts.NO_CONTEXT_ANSWER,
            model_used="none",
            input_tokens=embed_result.input_tokens,
            estimated_cost=embed_result.estimated_cost,
            latency_ms=latency,
            tenant_id=ctx.tenant_id,
            confidence=0.0,
            correlation_id=get_correlation_id(),
        )

    # -- 10. reranking (before context construction trims) ---------------
    hits = rerank(question, hits, top_k=settings.retrieval_top_k)

    # -- 8. context construction -----------------------------------------
    max_context_chars = settings.max_input_tokens * 4 - len(prompts.SYSTEM_PROMPT)
    context = prompts.build_context(hits, max_chars=max_context_chars)

    # -- 9. model routing (inside the provider: primary -> fallback) -----
    chat_result = await provider.chat(
        system=prompts.SYSTEM_PROMPT,
        messages=[prompts.build_user_message(context, question)],
        max_tokens=settings.max_output_tokens,
        temperature=0.1,
    )

    # -- 11. citation validation -----------------------------------------
    answer, citations, invented = guardrails.validate_citations(
        chat_result.text, context, ctx.tenant_id
    )

    # -- 12. output guardrail --------------------------------------------
    answer, block_reason = guardrails.apply_output_guardrail(answer)
    if block_reason:
        citations = []
        await repo.record_audit(
            session,
            event_type="guardrail.output_blocked",
            ctx=ctx,
            severity="error",
            reason=block_reason,
        )

    confidence = guardrails.compute_confidence(hits, citations, invented, no_context=False)

    # -- 13. token / cost tracking ---------------------------------------
    total_input = chat_result.input_tokens + embed_result.input_tokens
    total_cost = chat_result.estimated_cost + embed_result.estimated_cost
    latency = elapsed_ms()

    await repo.record_usage(
        session,
        ctx,
        operation="chat",
        model_used=chat_result.model_used,
        input_tokens=total_input,
        output_tokens=chat_result.output_tokens,
        estimated_cost=total_cost,
        latency_ms=latency,
    )

    citation_dicts = [asdict(c) for c in citations]

    # Only cache answers that passed every guard.
    if not block_reason and citations:
        await cache.store(
            ctx,
            question,
            query_vector,
            answer=answer,
            citations=citation_dicts,
            model_used=chat_result.model_used,
            confidence=confidence,
        )

    logger.info(
        "rag_answered",
        extra={
            "extra": {
                "hits": len(hits),
                "citations": len(citations),
                "invented_citations": invented,
                "confidence": confidence,
                "model": chat_result.model_used,
                "estimated_cost": total_cost,
                "latency_ms": latency,
            }
        },
    )

    # -- 14. language handling is instructed in the system prompt --------
    return ChatResponse(
        answer=answer,
        citations=citation_dicts,
        retrieved_chunks=[_chunk_summary(h) for h in context.used_hits],
        model_used=chat_result.model_used,
        input_tokens=total_input,
        output_tokens=chat_result.output_tokens,
        estimated_cost=round(total_cost, 8),
        latency_ms=latency,
        cache_hit=False,
        tenant_id=ctx.tenant_id,
        confidence=confidence,
        correlation_id=get_correlation_id(),
    )
