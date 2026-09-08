"""Knowledge chat - the RAG endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, RateLimitedUser
from app.core.exceptions import ValidationError
from app.db import repositories as repo
from app.db.models import Conversation, Message
from app.schemas import ChatRequest, ChatResponseOut, FeedbackRequest
from app.services.rag.pipeline import answer_question

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponseOut)
async def chat(payload: ChatRequest, ctx: RateLimitedUser, session: DbSession) -> ChatResponseOut:
    """Run the full RAG pipeline and persist the exchange.

    The tenant is taken from the verified token. ChatRequest deliberately has no
    tenant_id field - see .claude/rules/tenant-isolation.md section 2.
    """
    # Per-user daily cost ceiling, on top of the rate limits.
    spent_today = await repo.daily_spend(session, ctx)
    from app.core.config import settings

    if spent_today >= settings.daily_cost_ceiling_usd:
        raise ValidationError("You have reached today's AI usage limit for this account.")

    result = await answer_question(
        session,
        ctx,
        payload.question,
        department=payload.department.value if payload.department else None,
        document_ids=payload.document_ids,
    )

    # Persist conversation + messages, tenant-scoped.
    conversation = None
    if payload.conversation_id:
        conversation = await repo.get_conversation(session, ctx, payload.conversation_id)
    if conversation is None:
        conversation = Conversation(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            title=payload.question[:200],
            department=payload.department,
        )
        session.add(conversation)
        await session.flush()

    session.add(
        Message(
            tenant_id=ctx.tenant_id,
            conversation_id=conversation.id,
            role="user",
            content=payload.question,
            correlation_id=result.correlation_id,
        )
    )
    assistant = Message(
        tenant_id=ctx.tenant_id,
        conversation_id=conversation.id,
        role="assistant",
        content=result.answer,
        citations=result.citations,
        retrieved_chunks=result.retrieved_chunks,
        model_used=result.model_used,
        confidence=result.confidence,
        cache_hit=result.cache_hit,
        correlation_id=result.correlation_id,
    )
    session.add(assistant)
    await session.commit()

    return ChatResponseOut(
        **result.to_dict(),
        conversation_id=conversation.id,
    )


@router.post("/feedback", status_code=201)
async def submit_feedback(
    payload: FeedbackRequest, ctx: RateLimitedUser, session: DbSession
) -> dict[str, str]:
    feedback = await repo.add_feedback(
        session,
        ctx,
        message_id=payload.message_id,
        rating=payload.rating,
        reason=payload.reason,
        comment=payload.comment,
    )
    await session.commit()
    return {"id": feedback.id, "status": "recorded"}
