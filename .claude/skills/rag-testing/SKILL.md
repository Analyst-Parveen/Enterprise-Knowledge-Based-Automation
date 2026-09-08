---
name: rag-testing
description: Test and evaluate the RAG pipeline - retrieval quality, citation correctness, faithfulness, guardrail stages, caching, model routing, latency, and cost. Use when asked to test RAG, evaluate retrieval quality, check citations or hallucination, or after changing chunking, embeddings, prompts, or retrieval parameters.
---

# Skill: RAG Testing and Evaluation

Governed by [testing.md](../../rules/testing.md),
[guardrails.md](../../rules/guardrails.md), and
[ai-model-usage.md](../../rules/ai-model-usage.md).

## When this must run

Any change to these requires a full evaluation run before it is accepted:

- Embedding model or chunking strategy
- Retrieval parameters or the relevance threshold
- Reranking approach
- System prompts or a prompt release
- Model routing rules

## Part 1 — Pipeline stage tests

Each stage in PROJECT.md section 4 is tested independently. No stage may be
skipped in the implementation, and a cache hit does **not** bypass
authentication, tenant filtering, or the output guardrail.

| Stage | What to assert |
|---|---|
| Authentication | Unauthenticated request rejected 401 |
| Input validation | Oversized / malformed input rejected before retrieval |
| Injection scan | Known injection payloads detected, refused, security event emitted |
| Semantic cache | Second identical query returns `cache_hit: true`; no cross-tenant hit |
| Tenant filtering | Every Qdrant search carries a `tenant_id` must-filter |
| Retrieval | Relevant chunks returned for known questions |
| Relevance threshold | Low-score chunks discarded; empty result yields "not found", not a guess |
| Context construction | Token budget respected; chunk identifiers preserved |
| Model routing | Expected model chosen; reported in `model_used` |
| Reranking | Ordering improves over raw vector order |
| Citation validation | Citations map to actually-retrieved chunks in the caller's tenant |
| Output guardrail | Unsafe HTML/JS and system-prompt leakage blocked |
| Token/cost tracking | `request_usage` row written with tokens, cost, latency |
| Language handling | Non-English question answered appropriately |

## Part 2 — Retrieval quality

Run against fixed datasets in `evaluation/datasets/` with known
question -> expected-document pairs.

- **Precision** — fraction of retrieved chunks that are relevant
- **Recall / hit rate** — fraction of questions where the correct document appears
  in the top-k

Report per-department as well as overall — a strong overall number can hide a
department that retrieves badly.

## Part 3 — Answer quality

- **Answer relevance** — does the answer address the question?
- **Faithfulness** — is every claim grounded in the retrieved context? Hallucination
  is a failure, not a style issue.
- **Citation correctness** — do citations point to the document and page that
  actually support the claim?
- **Factual correctness** — against dataset ground truth.

Include negative cases: questions with no answer in the corpus must produce an
honest "not found in your knowledge base", never an invented answer. Track that
refusal rate explicitly.

## Part 4 — Operational metrics

- Latency: p50 / p95 / p99, cache-hit and cache-miss separately
- Token usage per query (input and output)
- Estimated cost per query, and projected cost at demo volume
- Cache hit rate

Cost matters here — the project runs on ~$140 of credit. Keep the evaluation
dataset small enough to run repeatedly without burning the budget, and record the
cost of each evaluation run.

## Part 5 — Agentic workflows

For each supported workflow (policy comparison, summarization, cross-document
analysis, knowledge extraction, report generation):

- Correct documents found
- Every node stays within the tenant
- Step and timeout bounds respected — no unbounded loops
- Output carries valid citations
- Partial failure degrades gracefully with a labelled partial result
- Token and cost usage accumulated into `request_usage`

## Running it

```bash
pytest backend/tests/evaluation -v
pytest backend/tests/security -k "injection or guardrail or tenant" -v
```

Unit and integration tests mock the model. The evaluation suite makes real calls
deliberately — never let real AI calls leak into the unit or integration suites.

## Reporting

Write a timestamped report to `docs/reports/` with the git SHA, the model and
prompt versions, chunking and retrieval parameters, and every metric above,
compared against the previous baseline.

**A regression must be explained before the change is accepted.** Do not report an
average that hides a specific failure — call out weak departments, weak modalities,
and any case where the system answered confidently but wrongly.
