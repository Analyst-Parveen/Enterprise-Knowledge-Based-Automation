"""AI provider interface, Bedrock implementation, and a $0 local dev provider.

Bedrock is the real path for chat, vision, and embeddings. The local provider
exists so the entire stack runs offline at zero cost during Phase 1-4 development
- it is deterministic and clearly labelled, and it must NEVER be used for
evaluation results or in a deployed environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Protocol

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.exceptions import UpstreamError
from app.core.logging import get_logger
from app.services.ai.registry import ModelRole, ModelSpec, estimate_cost, resolve

logger = get_logger(__name__)


@dataclass(slots=True)
class ChatResult:
    text: str
    model_used: str
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    stop_reason: str | None = None


@dataclass(slots=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model_used: str
    input_tokens: int
    estimated_cost: float
    dimension: int = field(default=0)


class AIProvider(Protocol):
    async def chat(
        self, *, system: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float
    ) -> ChatResult: ...

    async def embed(self, texts: list[str]) -> EmbeddingResult: ...

    async def describe_image(
        self, *, image_bytes: bytes, media_type: str, prompt: str
    ) -> ChatResult: ...


# ---------------------------------------------------------------------------
# token estimation (shared)
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """~4 characters per token. Good enough for budget tracking, not billing."""
    return max(1, len(text) // 4)


# ===========================================================================
# Bedrock
# ===========================================================================
class BedrockProvider:
    """Amazon Bedrock via the Converse API (uniform across model families)."""

    def __init__(self) -> None:
        cfg = BotoConfig(
            region_name=settings.bedrock_region,
            retries={"max_attempts": 3, "mode": "adaptive"},
            connect_timeout=10,
            read_timeout=120,
        )
        self._runtime = boto3.client("bedrock-runtime", config=cfg)

    # -- chat ------------------------------------------------------------
    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> ChatResult:
        spec = resolve(ModelRole.CHAT_PRIMARY)
        try:
            return await self._converse(spec, system, messages, max_tokens, temperature)
        except UpstreamError:
            fallback = resolve(ModelRole.CHAT_FALLBACK)
            if fallback.model_id == spec.model_id:
                raise
            # A fallback is never silent: it is logged and reported in model_used.
            logger.warning(
                "model_fallback",
                extra={"extra": {"from": spec.model_id, "to": fallback.model_id}},
            )
            return await self._converse(fallback, system, messages, max_tokens, temperature)

    async def _converse(
        self,
        spec: ModelSpec,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> ChatResult:
        def _call() -> dict[str, Any]:
            return self._runtime.converse(
                modelId=spec.model_id,
                system=[{"text": system}],
                messages=messages,
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            )

        try:
            response = await asyncio.to_thread(_call)
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "bedrock_chat_failed",
                extra={"extra": {"model": spec.model_id, "error": type(exc).__name__}},
            )
            raise UpstreamError("The AI service is unavailable.") from exc

        text = "".join(
            block.get("text", "")
            for block in response.get("output", {}).get("message", {}).get("content", [])
        )
        usage = response.get("usage", {})
        in_tok = int(usage.get("inputTokens", 0))
        out_tok = int(usage.get("outputTokens", 0))

        return ChatResult(
            text=text,
            model_used=spec.model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated_cost=estimate_cost(spec, in_tok, out_tok),
            stop_reason=response.get("stopReason"),
        )

    # -- vision ----------------------------------------------------------
    async def describe_image(
        self, *, image_bytes: bytes, media_type: str, prompt: str
    ) -> ChatResult:
        spec = resolve(ModelRole.VISION)
        # Guard: never send an image to a text-only model such as gpt-oss.
        from app.services.ai.registry import validate_vision_routing

        validate_vision_routing(spec)

        image_format = media_type.split("/")[-1].lower()
        if image_format == "jpg":
            image_format = "jpeg"

        message = {
            "role": "user",
            "content": [
                {"image": {"format": image_format, "source": {"bytes": image_bytes}}},
                {"text": prompt},
            ],
        }

        def _call() -> dict[str, Any]:
            return self._runtime.converse(
                modelId=spec.model_id,
                messages=[message],
                inferenceConfig={"maxTokens": 1024, "temperature": 0.0},
            )

        try:
            response = await asyncio.to_thread(_call)
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "bedrock_vision_failed",
                extra={"extra": {"model": spec.model_id, "error": type(exc).__name__}},
            )
            raise UpstreamError("The vision service is unavailable.") from exc

        text = "".join(
            block.get("text", "")
            for block in response.get("output", {}).get("message", {}).get("content", [])
        )
        usage = response.get("usage", {})
        in_tok = int(usage.get("inputTokens", 0))
        out_tok = int(usage.get("outputTokens", 0))
        return ChatResult(
            text=text,
            model_used=spec.model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated_cost=estimate_cost(spec, in_tok, out_tok),
        )

    # -- embeddings ------------------------------------------------------
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Titan v2 embeds one text per call; we batch with a thread pool."""
        spec = resolve(ModelRole.EMBEDDING)
        dimension = spec.dimension or settings.bedrock_embedding_dimension

        def _embed_one(text: str) -> list[float]:
            body = json.dumps({"inputText": text, "dimensions": dimension, "normalize": True})
            response = self._runtime.invoke_model(modelId=spec.model_id, body=body)
            payload = json.loads(response["body"].read())
            return list(payload["embedding"])

        try:
            vectors = await asyncio.gather(*(asyncio.to_thread(_embed_one, t) for t in texts))
        except (ClientError, BotoCoreError) as exc:
            logger.error(
                "bedrock_embed_failed",
                extra={"extra": {"model": spec.model_id, "error": type(exc).__name__}},
            )
            raise UpstreamError("The embedding service is unavailable.") from exc

        in_tok = sum(estimate_tokens(t) for t in texts)
        return EmbeddingResult(
            vectors=list(vectors),
            model_used=spec.model_id,
            input_tokens=in_tok,
            estimated_cost=estimate_cost(spec, in_tok, 0),
            dimension=dimension,
        )


# ===========================================================================
# Local dev provider - $0, offline, deterministic. NEVER for evaluation.
# ===========================================================================
class LocalProvider:
    """Deterministic stand-in so Phases 1-4 run with no AWS account and no cost.

    Embeddings are hashed bag-of-words projections: stable and similarity-aware
    enough to exercise retrieval plumbing, but semantically weak. Chat is
    extractive - it quotes the supplied context rather than generating.

    Never use this for evaluation metrics or in a deployed environment.
    """

    _TOKEN_RE = re.compile(r"[a-z0-9]+")

    def __init__(self) -> None:
        logger.warning(
            "local_ai_provider_active",
            extra={
                "extra": {
                    "warning": "AI_PROVIDER=local - deterministic stub, $0, NOT for evaluation"
                }
            },
        )

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> ChatResult:
        # Extractive: echo the most relevant lines of the provided context.
        last = messages[-1] if messages else {}
        text_blocks = [b.get("text", "") for b in last.get("content", []) if "text" in b]
        joined = "\n".join(text_blocks)

        question = joined.split("QUESTION:")[-1].strip() if "QUESTION:" in joined else joined
        q_terms = set(self._TOKEN_RE.findall(question.lower()))

        context_part = joined.split("QUESTION:")[0]
        lines = [ln.strip() for ln in context_part.splitlines() if len(ln.strip()) > 40]
        scored = sorted(
            lines,
            key=lambda ln: len(q_terms & set(self._TOKEN_RE.findall(ln.lower()))),
            reverse=True,
        )
        best = scored[:3]

        answer = (
            "Based on the retrieved context:\n\n" + "\n\n".join(best)
            if best
            else "I could not find that in your knowledge base."
        )
        in_tok = estimate_tokens(system + joined)
        out_tok = estimate_tokens(answer)
        return ChatResult(
            text=answer,
            model_used="local-dev-stub",
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated_cost=0.0,
            stop_reason="end_turn",
        )

    async def describe_image(
        self, *, image_bytes: bytes, media_type: str, prompt: str
    ) -> ChatResult:
        digest = hashlib.sha256(image_bytes).hexdigest()[:12]
        text = (
            f"[local-dev-stub] Image placeholder description (sha {digest}, "
            f"type {media_type}, {len(image_bytes)} bytes). "
            "Enable AI_PROVIDER=bedrock for real vision extraction."
        )
        return ChatResult(
            text=text,
            model_used="local-dev-stub",
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(text),
            estimated_cost=0.0,
        )

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        dim = settings.bedrock_embedding_dimension
        vectors = [self._hash_embed(t, dim) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            model_used="local-dev-stub",
            input_tokens=sum(estimate_tokens(t) for t in texts),
            estimated_cost=0.0,
            dimension=dim,
        )

    @classmethod
    def _hash_embed(cls, text: str, dim: int) -> list[float]:
        """Hashed bag-of-words -> L2-normalised vector. Deterministic."""
        vec = [0.0] * dim
        for token in cls._TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            idx = struct.unpack("<Q", digest)[0] % dim
            sign = 1.0 if digest[0] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            vec[0] = 1.0
            return vec
        return [v / norm for v in vec]


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------
_provider: AIProvider | None = None


def get_provider() -> AIProvider:
    global _provider
    if _provider is None:
        if settings.ai_provider == "local":
            if not settings.is_dev:
                raise RuntimeError("AI_PROVIDER=local is only permitted when ENVIRONMENT=dev")
            _provider = LocalProvider()
        else:
            _provider = BedrockProvider()
    return _provider


def reset_provider() -> None:
    """Test hook."""
    global _provider
    _provider = None
