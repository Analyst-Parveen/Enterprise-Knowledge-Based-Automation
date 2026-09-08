# Rule: Security

Scope: all backend, frontend, and infrastructure code.

## 1. Threat model

Every change must be checked against this list. If a change touches a listed
surface, the corresponding control must be present and tested.

| Threat | Required control |
|---|---|
| Direct prompt injection | Input injection scanner before retrieval (see [guardrails.md](guardrails.md)) |
| Indirect prompt injection | Retrieved chunks treated as untrusted data, never as instructions |
| System prompt extraction | Output guardrail blocks system-prompt echo |
| Instruction override | System prompt is immutable; user text never concatenated into it |
| Cross-tenant access | Mandatory `tenant_id` filter (see [tenant-isolation.md](tenant-isolation.md)) |
| Malicious files | MIME sniffing, extension allow-list, size cap, structure checks, sandboxed parsing |
| Unsafe HTML / JS | Sanitize all rendered model output; no `dangerouslySetInnerHTML` on model text |
| Path traversal | Never build S3 keys or filesystem paths from raw user input |
| Retrieval poisoning | Ingestion-time content scanning + provenance in citations |
| Token abuse | Per-user rate limits + max token caps + cost tracking |
| Sensitive-data leakage | PII/secret redaction in logs; output guardrail; least-privilege reads |
| Unauthorized deletion | Ownership or admin authorization required and audited |

## 2. Authentication and authorization

- Amazon Cognito issues JWTs. The backend **verifies signature, issuer, audience,
  and expiry** against the Cognito JWKS on every request. Never trust an unverified
  claim.
- Roles are exactly `user` and `admin`. No implicit roles.
- Authorization is enforced **server-side**. Hiding a button in the frontend is
  not authorization.
- `tenant_id` is taken from the verified token, **never** from a request body,
  query string, or header supplied by the client.
- Admin-only endpoints (metrics, audit logs, tenants, users, deployments) require
  an explicit role check in the route dependency.

## 3. Request and transport hardening

Required in the FastAPI application setup:

- `TrustedHostMiddleware` with an explicit allow-list.
- CORS as an explicit **allow-list** of origins. Never `allow_origins=["*"]`
  together with credentials.
- Security headers on every response: `Strict-Transport-Security`,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, and a restrictive `Content-Security-Policy`.
- HTTPS everywhere. HTTP redirects to HTTPS at the edge.
- Request body size limit and upload size limit enforced before parsing.

## 4. Rate limiting

Enforced per authenticated user, backed by Redis:

| Bucket | Limit |
|---|---|
| API requests | 20 / minute |
| Server-side (internal fan-out) requests | 10 / minute |
| Document uploads | 5 / minute |

Exceeding a limit returns `429` with a `Retry-After` header and emits a security
event.

## 5. File upload handling

1. Validate declared extension against the allow-list (PDF, MD, TXT, DOC, DOCX,
   CSV, XLSX, common image types, common audio/video types).
2. Verify actual content type by sniffing magic bytes. Reject on mismatch.
3. Enforce a maximum size before reading the whole body into memory.
4. Generate the S3 key server-side from a UUID plus `tenant_id`. Never from the
   user-supplied filename. Store the original filename as metadata only.
5. Parse documents in a constrained worker, never in the request handler.
6. S3 objects are encrypted at rest; buckets block all public access.

## 6. Containers and images

- Containers run as a **non-root** user with a read-only root filesystem where
  practical.
- No secrets in image layers, `ENV`, or build args.
- Amazon ECR image scanning enabled; a build with critical findings does not
  reach deploy.

## 7. Logging and audit

- Every request carries a correlation ID, propagated frontend to backend to AI
  calls to LangSmith.
- Never log: JWTs, raw credentials, full document contents, or PII. Redact before
  writing.
- Security-relevant events are written to `audit_events`: auth failures,
  authorization denials, rate-limit trips, injection detections, deletions,
  cross-tenant attempts, admin actions.

## 8. Hard prohibitions

- Never commit secrets, keys, tokens, or `.env` files. See
  [secrets-management.md](secrets-management.md).
- Never disable TLS verification.
- Never add an endpoint that accepts `tenant_id` from the client as the
  authorization source.
- Never render unsanitized model output as HTML.
- Never run destructive AWS commands against resources this project does not own.
  See [aws-infrastructure.md](aws-infrastructure.md).
