---
name: e2e-testing
description: Run complete end-to-end tests across the frontend, API, RAG pipeline, and agentic workflows. Use when asked to run E2E tests, test the whole system, run Playwright journeys, or run test-e2e.sh.
---

# Skill: End-to-End Testing

Governed by [testing.md](../../rules/testing.md).

## Constraints

- Never run E2E against production or any shared environment without explicit
  instruction. Target the ephemeral demo environment or local Docker Compose.
- Never weaken, skip, or delete a test to make the run green. Fix the code, or
  report the failure.
- Tests create their own tenants, users, and documents, and clean up afterwards.
- E2E may make real AI calls. Keep the suite small and deliberate, and record the
  cost. See [ai-model-usage.md](../../rules/ai-model-usage.md).

## Step 1 — Preconditions

```bash
./scripts/verify.sh     # environment must be healthy first
./scripts/seed.sh       # dashboards and search need data
```

Confirm the target environment, the git SHA under test, and that test credentials
come from configuration — never hardcoded.

## Step 2 — Required journeys

**Authentication**
1. Unauthenticated access to a protected page redirects to login.
2. Cognito login succeeds; JWT is issued and accepted.
3. Expired or invalid token is rejected with 401.
4. Logout clears the session.

**Document lifecycle**
5. Upload a PDF; ingestion job progresses to completed.
6. Upload an image/diagram; multimodal extraction produces searchable chunks.
7. Upload an audio file; Transcribe produces a transcript that becomes searchable.
8. Rejected uploads: oversized file, disallowed type, mismatched magic bytes,
   traversal filename.
9. Delete a document as its owner; confirm chunks are removed from Qdrant.
10. Deletion by a non-owner is denied and audited.

**Knowledge chat**
11. Ask a question answerable from a seeded document; verify the answer, the
    citations, and that citations link to real retrieved chunks.
12. Ask an unanswerable question; verify the system says it does not know rather
    than inventing an answer.
13. Repeat a question; verify `cache_hit: true` on the second call.
14. Verify the full response envelope is populated (all 11 fields).

**Tenant isolation** (a failure here is release-blocking)
15. A user of tenant A cannot see, search, cite, download, or delete tenant B
    documents anywhere in the UI or API.

**Agentic workflow**
16. Run the policy-comparison workflow across two seeded policy documents;
    verify the summary and its citations.

**Dashboards**
17. User pages render with seeded data: Dashboard, Knowledge Chat, Documents,
    Departments, Usage, Feedback.
18. Admin pages render: Users, Tenants, Documents, AI Metrics, Security,
    Audit Logs, Deployments.
19. A non-admin user cannot reach admin pages.

**Resilience**
20. Rate limiting surfaces a clear message at the configured thresholds.
21. Correlation ID is present in the browser request and traceable through to
    backend logs and LangSmith.

## Step 3 — Execute

```bash
./scripts/test-e2e.sh
# or, directly:
pytest backend/tests/integration backend/tests/security
npx playwright test --config frontend/playwright.config.ts
```

## Step 4 — Report

Write a timestamped report to `docs/reports/` covering: environment, git SHA,
journeys run, pass/fail per journey, failure evidence (screenshots/traces for
Playwright), latency and token/cost totals for the AI calls made.

Report failures plainly. A partially passing run is not a passing run — say which
journeys failed and what they mean for release readiness.

## Step 5 — Clean up

Remove test-created tenants, users, and documents. Confirm no orphaned S3 objects
or Qdrant points remain. Never delete seeded demo data that the dashboards depend on.
