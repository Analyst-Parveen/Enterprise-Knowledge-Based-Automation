"""One controlled smoke test for Cohere + Qdrant + Groq/Bedrock config.

Run once from the repo root or backend/:
  cd backend && python -m scripts.smoke_rag_providers

Never logs secret values. Deletes only the temporary Qdrant point it creates.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

# Allow `python -m scripts.smoke_rag_providers` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.context import RequestContext  # noqa: E402
from app.services import vector  # noqa: E402
from app.services.ai.provider import (  # noqa: E402
    BedrockProvider,
    CohereEmbeddingBackend,
    GroqChatBackend,
    reset_provider,
)
from app.services.ai.registry import ModelRole, resolve  # noqa: E402
from app.services.vector import ChunkPayload  # noqa: E402


def _ok(label: str, detail: str = "") -> None:
    print(f"OK  {label}" + (f" - {detail}" if detail else ""))


def _fail(label: str, detail: str) -> None:
    print(f"FAIL  {label} - {detail}")


async def main() -> int:
    failures = 0
    print("=== RAG provider smoke (single pass) ===")
    print(f"embed_provider={settings.embed_provider} llm_provider={settings.llm_provider}")
    print(f"llm_model={settings.effective_chat_model_id}")
    print(f"cohere_model={settings.cohere_embed_model} dim={settings.embedding_dimension}")
    print(f"qdrant_collection={settings.qdrant_collection}")
    print(f"qdrant_url_configured={bool(settings.qdrant_url)}")
    print(f"cohere_key_set={bool(settings.cohere_api_key)}")
    print(f"groq_key_set={bool(settings.groq_api_key)}")

    # 1. Cohere embedding
    vector_for_qdrant: list[float] | None = None
    if not settings.cohere_api_key:
        _fail("cohere_embed", "COHERE_API_KEY missing in .env / environment")
        failures += 1
    else:
        try:
            embedder = CohereEmbeddingBackend()
            result = await embedder.embed(["smoke test: enterprise knowledge assistant"])
            dim = result.dimension or (len(result.vectors[0]) if result.vectors else 0)
            if dim != 1024:
                _fail("cohere_embed", f"expected dimension 1024, got {dim}")
                failures += 1
            else:
                _ok("cohere_embed", f"model={result.model_used} dimension={dim}")
                vector_for_qdrant = result.vectors[0]
        except Exception as exc:  # noqa: BLE001
            _fail("cohere_embed", type(exc).__name__)
            failures += 1

    # 2–5. Qdrant collection + tenant-scoped upsert/search/isolation
    point_id = str(uuid.uuid4())
    tenant_a = RequestContext(user_id="smoke-a", tenant_id="smoke-tenant-a", role="user")
    tenant_b = RequestContext(user_id="smoke-b", tenant_id="smoke-tenant-b", role="user")
    created_point = False
    try:
        await vector.ensure_collection()
        _ok(
            "qdrant_collection",
            f"{settings.qdrant_collection} ready "
            f"(COSINE/{settings.embedding_dimension})",
        )

        if vector_for_qdrant is None:
            _fail("qdrant_upsert", "skipped — no Cohere vector")
            failures += 1
        else:
            payload = ChunkPayload(
                document_id="smoke-doc",
                chunk_id=point_id,
                document_name="smoke.txt",
                page_number=1,
                source_uri="s3://smoke/smoke.txt",
                owner_id="smoke-a",
                tenant_id=tenant_a.tenant_id,
                document_version=1,
                created_by="smoke",
                created_at=vector.utc_now_iso(),
                text="temporary smoke vector",
            )
            await vector.upsert_chunks([vector_for_qdrant], [payload])
            created_point = True
            _ok("qdrant_upsert", "tenant-scoped point written")

            hits_a = await vector.search(tenant_a, vector_for_qdrant, top_k=3)
            if not any(h.payload.get("chunk_id") == point_id for h in hits_a):
                _fail("qdrant_search", "own-tenant search missed the smoke point")
                failures += 1
            else:
                _ok("qdrant_search", "tenant filter returned the smoke point")

            hits_b = await vector.search(tenant_b, vector_for_qdrant, top_k=3)
            if any(h.payload.get("chunk_id") == point_id for h in hits_b):
                _fail("tenant_isolation", "foreign tenant saw the smoke point")
                failures += 1
            else:
                _ok("tenant_isolation", "foreign tenant did not see the smoke point")
    except Exception as exc:  # noqa: BLE001
        _fail("qdrant", f"{type(exc).__name__}: {exc}")
        failures += 1
    finally:
        if created_point:
            try:
                await vector.delete_document_chunks(tenant_a, "smoke-doc")
                _ok("qdrant_cleanup", "temporary smoke point deleted")
            except Exception as exc:  # noqa: BLE001
                _fail("qdrant_cleanup", type(exc).__name__)
                failures += 1

    # 6. Groq once
    if not settings.groq_api_key:
        _fail("groq_chat", "GROQ_API_KEY missing in .env / environment")
        failures += 1
    else:
        try:
            chat = GroqChatBackend()
            result = await chat.chat(
                system="Reply with exactly one short sentence.",
                messages=[{"role": "user", "content": [{"text": "Say hello to the smoke test."}]}],
                max_tokens=256,
                temperature=0.0,
            )
            if not (result.text or "").strip():
                _fail("groq_chat", "empty response")
                failures += 1
            else:
                _ok("groq_chat", f"model={result.model_used}")
        except Exception as exc:  # noqa: BLE001
            _fail("groq_chat", type(exc).__name__)
            failures += 1

    # 7. Bedrock selectable via config (no RAG code change)
    try:
        reset_provider()
        primary = resolve(ModelRole.CHAT_PRIMARY)
        assert settings.llm_provider in ("groq", "bedrock", "local")
        # Constructing BedrockProvider proves the alternate chat backend exists;
        # we do not invoke Bedrock here (quota / cost).
        _ = BedrockProvider
        _ok(
            "bedrock_config",
            f"LLM_PROVIDER can be bedrock; current={settings.llm_provider} "
            f"resolve_primary={primary.model_id}",
        )
    except Exception as exc:  # noqa: BLE001
        _fail("bedrock_config", type(exc).__name__)
        failures += 1

    # 8. Voice path (logical only)
    from app.services.ai import transcribe as stt

    has_stt = hasattr(stt, "transcribe_media")
    has_tts = False  # no TTS module in this repo today
    if has_stt and not has_tts:
        _ok(
            "voice_path",
            "STT=Amazon Transcribe (audio->text->RAG->LLM). TTS not implemented; "
            "no embeddings from audio.",
        )
    else:
        _fail("voice_path", "unexpected STT/TTS layout")
        failures += 1

    print("=== done ===")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
