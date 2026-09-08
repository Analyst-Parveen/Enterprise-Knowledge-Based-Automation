---
name: document-ingestion
description: Ingest enterprise documents into the knowledge base - PDF, Markdown, TXT, DOC/DOCX, CSV, Excel, images, diagrams, audio, and video. Use when adding documents, debugging an ingestion job, re-indexing a collection, or working on the extract/chunk/embed pipeline.
---

# Skill: Document Ingestion

Governed by [ai-model-usage.md](../../rules/ai-model-usage.md),
[tenant-isolation.md](../../rules/tenant-isolation.md),
[security.md](../../rules/security.md) section 5, and PROJECT.md section 3.

## Pipeline by modality

**PDF / Markdown / TXT / DOC / DOCX**
```
Extract text -> Chunk -> Embed -> Qdrant
```

**Images / diagrams / tables (including tables inside documents)**
```
Multimodal model -> Extract and understand content -> Chunk -> Embed -> Qdrant
```

**CSV / Excel**
```
Parse -> Structure-aware serialization (preserve headers and row context)
      -> Chunk -> Embed -> Qdrant
```

**Audio**
```
S3 -> Amazon Transcribe -> Transcript -> Chunk -> Embed -> Qdrant
```

**Video**
```
S3 -> Audio track -> Amazon Transcribe
   -> Optional key frames -> Multimodal model
   -> Combine knowledge -> Chunk -> Embed -> Qdrant
```

**Model routing for ingestion (all Bedrock):**

| Input | Model |
|---|---|
| Images, diagrams, tables, video key frames | `amazon.nova-lite-v1:0` (vision) |
| Text understanding / summarization | `openai.gpt-oss-20b-1:0` (text-only) |
| All embeddings | `amazon.titan-embed-text-v2:0` (1024-dim) |

**Hard rules:** the vision model is used for *understanding*, never for
embeddings. Never route an image to a `gpt-oss` model — they are text-only.
Embeddings come only from the configured Bedrock embedding model.

## Step 1 — Accept the file safely

Before anything is parsed:

1. Extension against the allow-list.
2. Magic-byte sniffing must agree with the declared type. Reject on mismatch.
3. Size cap enforced before reading the body into memory.
4. S3 key generated server-side as `<tenant_id>/<uuid>`. **Never** built from the
   user-supplied filename — the original name is metadata only.
5. Parse in a constrained worker, never in the request handler.

Uploads are rate limited to 5 per minute per user.

## Step 2 — Create the ingestion job

Write an `ingestion_jobs` row with status, tenant, owner, and correlation ID.
Status transitions are recorded so the UI can show real progress and so failures
are observable. Never fail silently.

## Step 3 — Extract

- Preserve page numbers, section headings, and table structure — citations depend
  on them.
- For scanned PDFs, route pages through the multimodal model.
- For video, transcribe the audio track first; extract key frames only when they
  add information the audio does not carry (cost discipline).

## Step 4 — Chunk

- Semantic/structural boundaries first (headings, sections, table rows), with a
  size limit and modest overlap.
- Every chunk keeps enough context to be independently meaningful.
- Chunking parameters are configuration, not scattered literals. Changing them
  requires an evaluation run — see [testing.md](../../rules/testing.md) section 5.

## Step 5 — Scan for retrieval poisoning

Scan extracted text for embedded instructions ("ignore previous instructions",
role manipulation, exfiltration directives). Flag suspicious chunks in metadata
so retrieval can quarantine or down-weight them, and emit a security event.
See [guardrails.md](../../rules/guardrails.md) section 3.

## Step 6 — Embed and store

Batch embedding calls. Every Qdrant point payload carries the full metadata set:

```
document_id, chunk_id, document_name, page_number, source_uri,
owner_id, tenant_id, document_version, created_by, created_at
```

`tenant_id` is **mandatory on every point** — a point without it is a bug that
breaks isolation. Take it from the verified token context, never from user input.

Confirm the embedding model's dimension matches the collection's dimension.
Never mix vectors from different embedding models in one collection.

## Step 7 — Verify the ingestion

- Job status is `completed` with a chunk count.
- A query against known content in the document retrieves it, with correct
  citations and page numbers.
- The document is invisible to another tenant.

## Failure handling

- Record the failure reason on the job and surface it in the UI.
- Partial ingestion is either completed or rolled back — never leave orphaned
  chunks in Qdrant or orphaned objects in S3.
- Track ingestion failures as an observability metric.

## Deletion and re-indexing

- Deleting a document requires ownership or admin authorization, and is audited.
- Deletion removes the S3 object, the database row, and **all** its Qdrant points.
- Re-indexing after an embedding model change means a new collection and a
  migration — never a partial in-place mix.
