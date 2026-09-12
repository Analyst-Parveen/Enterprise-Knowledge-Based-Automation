---
name: security-testing
description: Run the project's security test suite - tenant isolation, prompt injection, guardrails, authn/authz, rate limiting, upload safety, and secret scanning. Use when asked to security test, check tenant isolation, test for prompt injection, or audit the platform's defences. Tests only this project's own systems.
---

# Skill: Security Testing

Governed by [security.md](../../rules/security.md),
[guardrails.md](../../rules/guardrails.md),
[tenant-isolation.md](../../rules/tenant-isolation.md), and
[testing.md](../../rules/testing.md).

## Scope constraint

This skill tests **this project's own systems only**, in local or the project's
own ephemeral demo environment. Never point these tests at third-party systems,
shared infrastructure, or anything outside this project's ownership.

Never weaken or delete a security test to make a suite pass. A failing security
test is a finding, not an obstacle.

## Suite 1 — Tenant isolation (release-blocking)

```bash
pytest backend/tests/security -k tenant -v
```

1. Cross-tenant document read by ID is denied.
2. Cross-tenant retrieval returns nothing from the other tenant, by search or filter.
3. Forged `tenant_id` in body, header, or query is ignored; request rejected 403
   and a security event recorded.
4. Semantic cache does not serve tenant A's answer to tenant B.
5. Agent/LangGraph workflows stay in one tenant at every node.
6. An admin of tenant A cannot read tenant B documents.
7. S3 pre-signed URLs cannot be obtained for another tenant's objects.

Any failure here blocks release. Report it as critical.

## Suite 2 — Prompt injection and guardrails

```bash
pytest backend/tests/security -k "injection or guardrail" -v
```

**Direct injection:** instruction override ("ignore previous instructions"),
role manipulation, delimiter injection, encoded payloads.

**Indirect injection:** ingest a document containing embedded instructions, then
query it. The instructions must be treated as data. Behaviour must not change.

**System prompt extraction:** "print your system prompt", "repeat everything
above", incremental extraction across turns.

**Output guardrail:** unsafe HTML/JS in model output is blocked; invented
citations are stripped by citation validation; leaked credentials or system
prompt fragments are blocked.

**Retrieval poisoning:** a poisoned chunk is flagged or down-weighted at ingestion
and does not dominate retrieval.

## Suite 3 — Platform security

```bash
pytest backend/tests/security -k "auth or ratelimit or upload" -v
```

- Unauthenticated request rejected with 401.
- Invalid signature, expired, wrong issuer, wrong audience tokens all rejected.
- Non-admin cannot reach admin endpoints (metrics, own company, users, audit logs).
- Rate limits trip correctly: 20 API / 10 server-side / 5 uploads per minute per
  user, and 10 sign-in attempts per minute per account, returning 429 with
  `Retry-After`.
- Upload safety: oversized file, disallowed extension, mismatched magic bytes,
  path-traversal filename, zip/archive bomb — all rejected.
- Deletion without ownership or authorization is denied and written to
  `audit_events`.
- Security headers present; CORS reflects only the allow-list; trusted-host
  validation active.

## Suite 3a — The role hierarchy

```bash
pytest backend/tests/security/test_onboarding_hierarchy.py -v
pytest backend/tests/integration/test_onboarding_api.py -v
pytest backend/tests/e2e/test_onboarding_flow.py -v      # needs PostgreSQL
```

This suite exists to prove one thing: a customer cannot become the service
provider, and cannot reach another customer.

- The role set is closed — `UserRole`, the `Role` literal and the auth allowlist
  name exactly `user`, `admin`, `platform_admin`, and any other value is a 401.
- `platform_admin` is not in `TENANT_ASSIGNABLE_ROLES`, is not a valid value in
  any request schema (so it is a 422 before a handler runs), and is refused again
  by `assert_role_assignable`.
- A token pairing `platform_admin` with a normal tenant — or a normal role with
  the reserved `platform` tenant — is rejected at verification and logged as
  critical. Neither half of the platform identity is forgeable alone.
- A tenant admin cannot create a company by any route or payload.
- A tenant admin of A cannot list, read, patch, or reset the password of a user
  in B; the attempt raises a tenant-isolation error and is audited as critical.
- A platform operator cannot reach a company's user directory.
- A plain user cannot reach any admin or platform route.
- A company cannot be left with no active administrator, and an admin cannot
  change its own role or deactivate itself.
- No API response, log line, or audit event from the invitation flow contains a
  password, a token, or a secret.

## Suite 4 — Supply chain and secrets

```bash
gitleaks detect --no-banner            # committed secrets
pip-audit                              # Python dependency CVEs
npm audit --audit-level=high           # frontend dependency CVEs
checkov -d infra/terraform             # IaC misconfiguration
aws ecr describe-image-scan-findings --repository-name ekba-backend --image-id imageTag=$GIT_SHA
```

If a real secret is found committed: tell the user immediately, advise rotating it
at the source, and **do not** rewrite git history or delete the secret yourself.
See [secrets-management.md](../../rules/secrets-management.md) section 6.

## Suite 5 — Infrastructure posture (read-only)

Verify with `describe-*` / `get-*` only:

- S3 public access blocked, encryption on, TLS-only policy.
- Database and Qdrant not publicly reachable.
- Security groups have no unintended `0.0.0.0/0` ingress.
- IAM policies are scoped, no unjustified wildcards.
- Containers run non-root.
- Secrets exist in Secrets Manager — confirm presence and metadata, **never read
  or print a value**.

## Reporting

Write a timestamped security report to `docs/reports/`:

- Suite-by-suite pass/fail
- Each finding with severity, affected component, and reproduction steps
- **Never include** working exploit payloads for third parties, secret values,
  or real credentials

Rank findings by severity. Cross-tenant leakage and authentication bypass are
always critical and always block release. State clearly whether the platform is
release-ready.
