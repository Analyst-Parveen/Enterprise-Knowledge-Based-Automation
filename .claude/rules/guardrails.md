# Rule: Guardrails

Guardrails are pipeline stages, not optional decorations. The RAG pipeline order
in PROJECT.md section 4 is mandatory and each guard stage must be independently
testable.

## 1. Trust boundaries

| Source | Trust level | Handling |
|---|---|---|
| System prompt | Trusted | Immutable, never built from user or document text |
| User question | Untrusted | Validated and scanned before use |
| Retrieved chunks | Untrusted **data** | Wrapped as data, never interpreted as instructions |
| Tool output | Untrusted | Validated before being returned to the model |
| Model output | Untrusted | Filtered before reaching the user |

The core defence against indirect prompt injection is that retrieved content is
always presented to the model inside an explicit data envelope with an
instruction that content inside it is reference material only.

## 2. Input stage

Before any retrieval:

1. **Validation** — length caps, encoding checks, reject control characters and
   oversized payloads.
2. **Injection scan** — pattern and heuristic detection for: instruction override
   ("ignore previous instructions"), system prompt extraction ("print your
   system prompt", "repeat everything above"), role manipulation, exfiltration
   attempts, encoded payloads (base64/hex blobs), and delimiter injection.
3. On detection: refuse or degrade, emit a security event with the correlation
   ID, and never forward the raw payload into the model prompt.

## 3. Retrieval stage

- Tenant and permission filters applied first (see
  [tenant-isolation.md](tenant-isolation.md)).
- **Relevance threshold**: chunks below the configured score are discarded. If
  nothing survives, the system answers "not found in your knowledge base" rather
  than inventing an answer.
- **Retrieval poisoning defence**: chunks are scanned at ingestion time for
  embedded instructions; suspicious content is flagged in metadata and either
  quarantined or down-weighted at retrieval.

## 4. Context construction

- Deterministic template. User text and document text occupy clearly separated,
  labelled regions.
- Token budget enforced by trimming lowest-ranked chunks, never by silently
  truncating the system prompt.
- Every included chunk keeps its identifiers so citations can be validated later.

## 5. Output stage

Applied in order, before the response leaves the service:

1. **Citation validation** — every citation must map to a chunk that was actually
   retrieved for this request, in this tenant. Unmapped or invented citations are
   removed and the confidence score is reduced.
2. **Output guardrail** — block system prompt disclosure, leaked credentials or
   keys, PII beyond what the source contains, and unsafe HTML/JS/script payloads.
3. **Sanitization** — model output rendered in the frontend is sanitized; links
   and images from model output are not auto-loaded from arbitrary origins.
4. **Confidence** — derived from retrieval scores and citation coverage, and
   returned in the response.

## 6. Refusal behaviour

The system refuses, clearly and briefly, when:

- The question requires data outside the caller's tenant.
- Retrieval returns nothing above threshold.
- An injection attempt is detected.
- The output guardrail blocks the generated answer.

A refusal still returns the full response envelope (tokens, cost, latency,
correlation ID) so observability stays intact.

## 7. Non-negotiables

- No guard stage may be skipped for performance. If a stage is slow, optimize the
  stage.
- A cache hit does **not** skip authentication, tenant filtering, or the output
  guardrail.
- Guardrail decisions are logged as security events, with the reason code, never
  the raw offending payload.
- Every guard stage has a dedicated test in `backend/tests/security/`.
