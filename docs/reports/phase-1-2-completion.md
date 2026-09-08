# Phase 1 + Phase 2 — Completion Report

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Phases | 1 (Core platform) and 2 (Multimodal + Bedrock) |
| Environment | Local only |
| AWS spend | **$0.00** — nothing was deployed |
| Tests | **136 passed, 0 failed** |
| Lint / format | ruff check + ruff format: clean (58 files) |

---

## Result

Both phases are complete and verified locally. No AWS resources were created,
so this cost nothing against the $20 ceiling.

## What was built

**Phase 1 — core platform**

- FastAPI app with typed settings, JSON logging with secret redaction,
  correlation-ID propagation, and centralized error handling that leaks no
  internals.
- All 10 database entities with Alembic migration `0001`. Every tenant-scoped
  table has a non-nullable, indexed `tenant_id`.
- Cognito JWT verification (signature, issuer, audience, expiry) with RBAC.
- Tenant-scoped repository layer — the only sanctioned path to tenant data.
- S3 storage with tenant-prefixed, server-generated keys.
- Qdrant integration where the tenant filter is built internally and cannot be
  overridden by a caller.
- Redis rate limiting at 20 / 10 / 5 per minute per user.
- Docker Compose stack: PostgreSQL, Qdrant, Redis, MinIO, API.
- 14 API endpoints across health, documents, chat, and admin.

**Phase 2 — multimodal and Bedrock**

- Bedrock provider (Converse API) for chat, vision, and Titan v2 embeddings,
  with an explicit logged fallback from primary to fallback chat model.
- Model registry mapping logical roles to model IDs, with a routing guard that
  refuses to send an image to a text-only model.
- Extractors for PDF, Markdown, TXT, DOCX, CSV, XLSX, images, audio, and video.
- Amazon Transcribe for audio and video (it reads mp4/mov/webm audio tracks
  directly, so there is no ffmpeg dependency anywhere).
- Structural chunker preserving page numbers and section headings for citations.
- Full RAG pipeline: all 14 stages of PROJECT.md section 4, in order.
- Semantic cache, BM25+vector reranking, citation validation, output guardrails,
  and token/cost tracking.
- Async ingestion with job status and rollback on failure.

## A deliberate addition: the $0 local provider

`AI_PROVIDER=local` provides deterministic hashed-bag-of-words embeddings and
extractive answers, so the entire stack runs offline with no AWS account. It is
hard-gated to `ENVIRONMENT=dev` (a test asserts it refuses to start elsewhere)
and is never valid for evaluation results.

This is what makes Phases 1–4 genuinely cost $0 rather than "nearly $0".

## Bugs found and fixed during verification

Four real defects, all caught by tests rather than review:

1. **Chunker default-collapse** — `(overlap_tokens or default)` treated an
   explicit `0` as "unset", silently applying a 64-token overlap and producing
   chunks 64% over budget. Fixed with `is None` checks plus a validation guard.
2. **Rerank normalization** — min-max normalizing cosine scores across a small
   candidate set stretched a 0.02 similarity gap into a decisive one, drowning
   the lexical signal and ranking an irrelevant chunk first. Now only BM25 is
   normalized; cosine is used raw.
3. **Path traversal check ordering** — `..` was checked *after* directory
   components were stripped, so the check never fired. Now checked on the
   original filename.
4. **Dependency ordering** — the DB session resolved before authentication, so
   unauthenticated requests opened a database connection before being rejected
   (returning 500 instead of 401 when the DB was down). Parameters reordered
   across all routes, with a test that fails if it regresses.

## Test coverage

| Suite | Tests | Covers |
|---|---|---|
| security | 76 | Tenant isolation, injection, guardrails, upload safety, RBAC, dev-auth gating |
| integration | 33 | Full RAG pipeline (14 stages), API request path, auth, headers, contracts |
| unit | 27 | Extractors, chunker, rerank, model registry, local provider |

All 17 mandatory security tests from `.claude/rules/testing.md` section 2 are
present and passing.

## Verified behaviours worth naming

- A user of tenant A retrieving tenant B's content gets nothing and an honest
  "not found" — not a fabricated answer.
- A cache hit still runs the output guardrail; a poisoned cache entry containing
  `<script>` is neutralized on the way out.
- An invented citation `[S9]` is stripped from the answer and lowers confidence.
- An answer containing an AWS key shape is blocked and never cached.
- Prompt injection is refused before retrieval or any model call, and the
  blocked request still records usage so observability stays intact.
- `ChatRequest` has no `tenant_id` field, and a client sending one is ignored.

## Not done, and why

- **Full-stack container run.** Docker Desktop was not running on this machine,
  so PostgreSQL/Qdrant/Redis were exercised through in-memory doubles rather
  than live containers. The compose file and migration are written but have not
  been executed. **This is the one gap** — run
  `docker compose -f infra/docker/docker-compose.yml up -d` then
  `alembic upgrade head` to close it.
- **Real Bedrock calls.** The Bedrock provider is written against the Converse
  API but has not been called with live credentials. Verify model availability
  in your region first:
  `aws bedrock list-foundation-models --region $AWS_REGION`
- **Video key-frame extraction.** Deliberately omitted — it needs ffmpeg or
  OpenCV as a system dependency and adds vision-model cost for marginal
  retrieval gain. Audio transcription covers the informational content.
- **mypy** is configured but was not run; ruff covers lint and format.

## Next

Phase 3 (LangGraph agents, evaluation harness) or Phase 4 (Next.js frontend).
Neither requires AWS.
