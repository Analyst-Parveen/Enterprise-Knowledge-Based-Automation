# Rule: Tenant Isolation

This is the single most important invariant in the project. A cross-tenant data
leak is a total failure of the product, regardless of how well anything else works.

## 1. The invariant

> No request, query, retrieval, cache read, agent step, log line, or response may
> ever expose data belonging to a tenant other than the caller's authorized tenant.

## 2. Source of truth for `tenant_id`

- `tenant_id` comes **only** from the verified Cognito JWT claims.
- It is resolved once, at the authentication dependency, and carried in a
  request-scoped context object.
- A `tenant_id` present in a request body, path, query string, or header is
  **ignored for authorization**. If it disagrees with the token, the request is
  rejected with `403` and a security event is recorded.
- Admin role does **not** imply cross-tenant data access. Admins get operational
  metrics, not other tenants' documents. Any genuine cross-tenant admin view must
  be an explicitly separate, explicitly audited endpoint.

## 3. Database layer

- Every table holding tenant data has a non-nullable `tenant_id` column with an
  index.
- Every query that reads tenant data filters on `tenant_id`. No exceptions.
- Prefer a single enforced access path: repository/service functions that require
  a tenant context argument, rather than raw session queries scattered through
  routes.
- Row-level security in PostgreSQL is a defence-in-depth addition, never the only
  control.

## 4. Vector layer (Qdrant)

- Every point payload includes `tenant_id` (see PROJECT.md section 6).
- **Every** search call attaches a `must` filter on `tenant_id`. A retrieval
  function that can be called without a tenant filter must not exist.
- Build the filter inside the retrieval service from the request context. Never
  accept a caller-supplied filter object that could omit or override it.
- Deletion and re-indexing operations are likewise tenant-scoped.

## 5. Cache layer (Redis)

- Every cache key is namespaced with `tenant_id`, for both the semantic cache and
  any ordinary cache.
- Semantic cache lookups match within a tenant only. A semantically similar
  question from another tenant must never return a cached answer.
- Rate-limit keys are namespaced by user and tenant.

## 6. Storage layer (S3)

- Object keys are prefixed with `tenant_id`.
- Access is mediated by the backend. No direct client access to arbitrary keys.
- Pre-signed URLs are generated only after an ownership/tenant check and are
  short-lived.

## 7. Agents and workflows

- LangGraph state carries the tenant context from the entry node onward.
- Every tool or node that touches data receives and applies the tenant filter.
  A tool that queries without one is a bug, not an optimization.
- Cross-document analysis stays inside one tenant.

## 8. Response and telemetry

- The chat response echoes `tenant_id` so mismatches are detectable in tests.
- Citations may only reference documents in the caller's tenant. Citation
  validation re-checks this before the response is returned.
- Metrics and logs are tagged with `tenant_id` but must not carry document content.

## 9. Required tests

These live in `backend/tests/security/` and must exist before any tenant-facing
feature is considered complete:

1. User of tenant A cannot retrieve, cite, download, or delete a document of
   tenant B, by ID or by search.
2. A forged `tenant_id` in body/header/query is ignored and rejected.
3. Semantic cache does not leak an answer across tenants.
4. Agent workflows do not cross tenants at any node.
5. An admin of tenant A cannot read documents of tenant B.
6. Deleting a document requires ownership or authorized admin, and is audited.

Any change to retrieval, caching, agents, or storage must run these tests.
