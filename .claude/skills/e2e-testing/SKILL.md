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
1. Unauthenticated access to a protected page shows the sign-in gate.
2. Email + password sign-in succeeds; the ID token is issued and accepted.
3. Expired or invalid token is rejected with 401.
4. Sign-out revokes the session server-side and clears it locally; going back to
   a protected page does not restore it.
5. Wrong credentials give one generic message that does not reveal whether the
   account exists.
6. A first sign-in on an invited account must set a password before any session
   is issued.
7. Password recovery is reachable and returns to sign-in.
8. An expiring session renews silently instead of interrupting the user.

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
18. Company admin pages render: Users, My Company, Documents, AI Metrics,
    Security, Audit Logs, Deployments.
19. Platform pages render: Companies, Onboarding Trail.
20. A non-admin user cannot reach admin pages or platform pages.

**Onboarding hierarchy** (a failure here is release-blocking)
21. A platform operator onboards a company, invites its administrator, and that
    administrator signs in and creates its own users.
22. A company admin's invite form offers only `user` and `admin` — no platform
    role, in the markup or the API.
23. A company admin cannot reach the platform pages or create a company.
24. A platform operator cannot manage a company's users.
25. Two companies' administrators cannot see each other's users.
26. No response or rendered page from the invitation flow contains a password.

**Resilience**
27. Rate limiting surfaces a clear message at the configured thresholds,
    including repeated sign-in attempts for one account.
28. Correlation ID is present in the browser request and traceable through to
    backend logs and LangSmith.

## Step 3 — Execute

```bash
./scripts/test-e2e.sh
# or, directly:
pytest backend/tests/integration backend/tests/security backend/tests/e2e
npx playwright test --config frontend/playwright.config.ts
```

`backend/tests/e2e` needs PostgreSQL. `test-e2e.sh` distinguishes "the stack is
down" from "the journey failed" and reports the former as a skip, because a skip
recorded as a pass is the one outcome that makes this suite worthless.

## Step 4 — Report

Write a timestamped report to `docs/reports/` covering: environment, git SHA,
journeys run, pass/fail per journey, failure evidence (screenshots/traces for
Playwright), latency and token/cost totals for the AI calls made.

Report failures plainly. A partially passing run is not a passing run — say which
journeys failed and what they mean for release readiness.

## Step 5 — Clean up

Remove test-created tenants, users, and documents. Confirm no orphaned S3 objects
or Qdrant points remain. Never delete seeded demo data that the dashboards depend on.
