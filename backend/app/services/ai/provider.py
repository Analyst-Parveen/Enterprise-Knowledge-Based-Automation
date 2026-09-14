"""AI provider interface and backends.

Chat and embeddings are selected independently via LLM_PROVIDER / EMBED_PROVIDER
(Groq or Bedrock for chat; Cohere or Bedrock for embeddings). RAG code always
calls get_provider() and never names a vendor. AI_PROVIDER=local remains the $0
offline stub for development and tests.
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
import httpx
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
    # Matches the "[S1] Document.pdf (page 4)" headers built by prompts.build_context
    _SOURCE_HEADER_RE = re.compile(r"^\[S(\d+)\]\s")

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
        # Extractive: echo the most relevant lines of the provided context,
        # tagged with the [Sn] marker of the source they were taken from.
        #
        # The marker is not decoration - this stub genuinely quotes that source
        # verbatim, so citation validation maps it to a real retrieved chunk and
        # the demo exercises the citation path rather than skipping it.
        last = messages[-1] if messages else {}
        text_blocks = [b.get("text", "") for b in last.get("content", []) if "text" in b]
        joined = "\n".join(text_blocks)

        question = joined.split("QUESTION:")[-1].strip() if "QUESTION:" in joined else joined
        q_terms = set(self._TOKEN_RE.findall(question.lower()))

        # Walk the SOURCES envelope, tracking which [Sn] block each line is under.
        context_part = joined.split("QUESTION:")[0]
        current_source: int | None = None
        candidates: list[tuple[int, str, int | None]] = []

        for raw_line in context_part.splitlines():
            line = raw_line.strip()
            header = self._SOURCE_HEADER_RE.match(line)
            if header:
                current_source = int(header.group(1))
                continue
            if len(line) <= 40 or line.startswith("<<<"):
                continue
            overlap = len(q_terms & set(self._TOKEN_RE.findall(line.lower())))
            candidates.append((overlap, line, current_source))

        # Only quote lines that actually share a term with the question.
        ranked = sorted(candidates, key=lambda c: c[0], reverse=True)
        best = [c for c in ranked[:3] if c[0] > 0]

        if best:
            quoted = [f"{line} [S{source}]" if source else line for _overlap, line, source in best]
            answer = "Based on the retrieved context:\n\n" + "\n\n".join(quoted)
        else:
            answer = "I could not find that in your knowledge base."
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
        dim = settings.embedding_dimension
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


# ===========================================================================
# Cohere embeddings
# ===========================================================================
class CohereEmbeddingBackend:
    """Cohere embed-multilingual-v3.0 (1024-d). Chat is not handled here."""

    def __init__(self) -> None:
        if not settings.cohere_api_key:
            raise UpstreamError("COHERE_API_KEY is not configured.")
        self._model = settings.cohere_embed_model
        self._dimension = settings.embedding_dimension

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        # search_document for batches (ingestion); search_query for a single probe.
        input_type = "search_query" if len(texts) == 1 else "search_document"
        headers = {
            "Authorization": f"Bearer {settings.cohere_api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = {
            "model": self._model,
            "texts": texts,
            "input_type": input_type,
            "embedding_types": ["float"],
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    "https://api.cohere.com/v1/embed", headers=headers, json=body
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "cohere_embed_failed",
                extra={"extra": {"model": self._model, "error": type(exc).__name__}},
            )
            raise UpstreamError("The embedding service is unavailable.") from exc

        embeddings = payload.get("embeddings") or {}
        vectors = embeddings.get("float") if isinstance(embeddings, dict) else embeddings
        if not isinstance(vectors, list) or not vectors:
            raise UpstreamError("The embedding service returned no vectors.")

        dim = len(vectors[0])
        if dim != self._dimension:
            raise UpstreamError(
                f"Cohere returned dimension {dim}; configured embedding dimension is "
                f"{self._dimension}."
            )

        in_tok = int(payload.get("meta", {}).get("billed_units", {}).get("input_tokens", 0)) or sum(
            estimate_tokens(t) for t in texts
        )
        spec = resolve(ModelRole.EMBEDDING)
        return EmbeddingResult(
            vectors=[list(map(float, v)) for v in vectors],
            model_used=self._model,
            input_tokens=in_tok,
            estimated_cost=estimate_cost(spec, in_tok, 0),
            dimension=dim,
        )


# ===========================================================================
# Groq chat (OpenAI-compatible)
# ===========================================================================
def _flatten_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert Bedrock-style content blocks to OpenAI/Groq string content."""
    out: list[dict[str, str]] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and "text" in block
            ]
            text = "".join(parts)
        else:
            text = str(content)
        out.append({"role": str(message.get("role", "user")), "content": text})
    return out


class GroqChatBackend:
    """Groq chat completions. Embeddings and vision are not handled here."""

    def __init__(self) -> None:
        if not settings.groq_api_key:
            raise UpstreamError("GROQ_API_KEY is not configured.")
        self._model = settings.effective_chat_model_id

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> ChatResult:
        headers = {
            "Authorization": f"Bearer {settings.groq_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": system}, *_flatten_messages(messages)],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{settings.groq_api_base.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "groq_chat_failed",
                extra={"extra": {"model": self._model, "error": type(exc).__name__}},
            )
            raise UpstreamError("The AI service is unavailable.") from exc

        choice = (body.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = body.get("usage") or {}
        in_tok = int(usage.get("prompt_tokens") or estimate_tokens(system))
        out_tok = int(usage.get("completion_tokens") or estimate_tokens(text))
        spec = resolve(ModelRole.CHAT_PRIMARY)
        return ChatResult(
            text=text,
            model_used=self._model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated_cost=estimate_cost(spec, in_tok, out_tok),
            stop_reason=choice.get("finish_reason"),
        )


# ===========================================================================
# Routing facade - RAG code stays provider-agnostic
# ===========================================================================
class RoutingProvider:
    """Delegates chat / embed / vision to the configured backends.

    Switching LLM_PROVIDER or EMBED_PROVIDER never requires RAG code changes.
    """

    def __init__(
        self,
        *,
        chat: Any,
        embed: Any,
        vision: Any | None = None,
    ) -> None:
        self._chat = chat
        self._embed = embed
        self._vision = vision

    async def chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
    ) -> ChatResult:
        return await self._chat.chat(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        return await self._embed.embed(texts)

    async def describe_image(
        self, *, image_bytes: bytes, media_type: str, prompt: str
    ) -> ChatResult:
        if self._vision is None:
            raise UpstreamError("Vision is not configured for the active AI providers.")
        return await self._vision.describe_image(
            image_bytes=image_bytes, media_type=media_type, prompt=prompt
        )


def _build_real_provider() -> AIProvider:
    """Compose chat + embed backends from EMBED_PROVIDER / LLM_PROVIDER."""
    bedrock: BedrockProvider | None = None

    def bedrock_shared() -> BedrockProvider:
        nonlocal bedrock
        if bedrock is None:
            bedrock = BedrockProvider()
        return bedrock

    if settings.embed_provider == "cohere":
        embed_backend: Any = CohereEmbeddingBackend()
    elif settings.embed_provider == "bedrock":
        embed_backend = bedrock_shared()
    else:
        raise UpstreamError("EMBED_PROVIDER=local requires AI_PROVIDER=local.")

    if settings.llm_provider == "groq":
        chat_backend: Any = GroqChatBackend()
    elif settings.llm_provider == "bedrock":
        chat_backend = bedrock_shared()
    else:
        raise UpstreamError("LLM_PROVIDER=local requires AI_PROVIDER=local.")

    # Vision stays on Bedrock when available; Groq is text-only.
    vision_backend: Any | None = None
    try:
        vision_backend = bedrock_shared()
    except Exception:  # noqa: BLE001
        vision_backend = None

    return RoutingProvider(chat=chat_backend, embed=embed_backend, vision=vision_backend)


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------
_provider: AIProvider | None = None


def get_provider() -> AIProvider:
    global _provider
    if _provider is None:
        if settings.ai_provider == "local" or settings.llm_provider == "local":
            if not settings.is_dev:
                raise RuntimeError("AI_PROVIDER=local is only permitted when ENVIRONMENT=dev")
            _provider = LocalProvider()
        elif (
            settings.ai_provider == "bedrock"
            and settings.embed_provider == "bedrock"
            and settings.llm_provider == "bedrock"
        ):
            # Pure Bedrock path kept for AWS demos that have not adopted Groq/Cohere.
            _provider = BedrockProvider()
        else:
            _provider = _build_real_provider()
    return _provider


def reset_provider() -> None:
    """Test hook."""
    global _provider
    _provider = None
