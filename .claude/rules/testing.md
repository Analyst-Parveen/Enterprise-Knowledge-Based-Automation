# Rule: Testing

Testing stack per PROJECT.md: **Pytest** (backend, security, evaluation) and
**Playwright** (frontend E2E).

## 1. Test layout

```
backend/tests/unit/          fast, isolated, all external I/O mocked
backend/tests/integration/   real Postgres + Qdrant + Redis via Docker Compose
backend/tests/security/      threat-model tests (MANDATORY, see below)
backend/tests/evaluation/    RAG quality metrics against fixed datasets
frontend/tests/e2e/          Playwright user journeys
```

## 2. Mandatory security tests

These are not optional and are not deleted to make a build green. Every one of
these must exist and pass before a feature that touches the relevant surface ships:

**Tenant isolation** (see [tenant-isolation.md](tenant-isolation.md) section 9)
1. Cross-tenant document read by ID is denied.
2. Cross-tenant retrieval returns nothing from the other tenant.
3. Forged `tenant_id` in body/header/query is ignored, request rejected.
4. Semantic cache does not serve one tenant's answer to another.
5. Agent workflows stay within one tenant at every node.
6. Admin of tenant A cannot read tenant B documents.

**Guardrails** (see [guardrails.md](guardrails.md))
7. Direct prompt injection is detected and refused.
8. Indirect injection embedded in an ingested document does not alter behaviour.
9. System prompt extraction attempts are blocked.
10. Invented citations are stripped by citation validation.
11. Output guardrail blocks unsafe HTML/JS in model output.

**Platform security** (see [security.md](security.md))
12. Unauthenticated request is rejected with 401.
13. Expired/invalid/wrong-issuer JWT is rejected.
14. Non-admin cannot reach admin endpoints.
15. Rate limits return 429 at the configured thresholds (20 / 10 / 5 per minute).
16. Malicious upload — wrong magic bytes, oversized file, traversal filename — is
    rejected.
17. Deletion without ownership or authorization is denied and audited.

## 3. Coverage expectations

- Security, tenant isolation, and the RAG pipeline stages: high coverage, tested
  directly.
- Services: unit tested with external dependencies mocked.
- API: integration tested against real infrastructure containers.
- Frontend: E2E covers login, chat with citations, upload and ingestion status,
  document list, admin metrics, and the tenant-boundary path.

## 4. Test discipline

- Tests are deterministic. No dependence on wall-clock time, network flakiness,
  or live model output. Mock the model in unit and integration tests.
- No live AI provider calls in unit or integration tests — they cost money and
  are non-deterministic. Real calls belong to the evaluation suite and to E2E
  smoke tests, both run deliberately.
- Fixtures build their own data and clean up. Tests never depend on each other's
  ordering or leftovers.
- **Never weaken or delete a test to make a build pass.** Fix the code. If a test
  is genuinely wrong, say so explicitly and explain why before changing it.
- Never point tests at production or shared AWS resources.

## 5. Evaluation suite

Datasets live in `evaluation/datasets/`. Metrics per PROJECT.md section 11:

retrieval precision, retrieval recall / hit rate, answer relevance, faithfulness,
citation correctness, factual correctness, latency, token usage, estimated cost.

Run the evaluation suite whenever any of these change:

- Embedding model or chunking strategy
- Retrieval parameters or relevance threshold
- Reranking approach
- System prompts or prompt release
- Model routing rules

Results are written to `docs/reports/` with a timestamp and the git SHA. A
regression against the previous baseline must be explained before the change is
accepted.

## 6. Local first

Everything runs locally against Docker Compose (PostgreSQL, Qdrant, Redis) before
any AWS deployment. A change that has not passed locally does not get deployed.

## 7. CI gates

The pipeline fails — and does not deploy — if any of these fail:

```
lint + format + type check
unit tests
integration tests
security tests
secret scan
dependency vulnerability scan
container image scan
terraform fmt + validate + security scan
E2E tests against the deployed environment
```
