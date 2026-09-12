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
- Roles are exactly `user`, `admin` and `platform_admin`. No implicit roles, and
  no role outside that set: a token carrying anything else is rejected, not
  downgraded.
- Authorization is enforced **server-side**. Hiding a button in the frontend is
  not authorization.
- `tenant_id` is taken from the verified token, **never** from a request body,
  query string, or header supplied by the client.
- Admin-only endpoints (metrics, audit logs, own company, users, deployments)
  require an explicit role check in the route dependency.

### The role hierarchy

`platform_admin` is the service provider's own role. It exists so that creating
a customer account is not something a customer can do.

| Role | Lives in | May do | May never do |
|---|---|---|---|
| `platform_admin` | the reserved `platform` tenant | create companies, invite each company's first admin, read the onboarding trail | read any company's documents, conversations, metrics or chat |
| `admin` | its own company | manage users **inside its own company**, assign `user` or `admin`, read its own company's metrics and audit log | create a company, create a `platform_admin`, touch another company |
| `user` | its own company | use the product | manage users or companies |

Three rules make the hierarchy hold, and all three are enforced in code:

1. **The platform role and the platform tenant imply each other.**
   `_claims_to_context` rejects a token where `role == "platform_admin"` and
   `tenant_id != "platform"`, and equally one where the tenant is `platform` but
   the role is not. Neither half is forgeable alone, so tenant filtering stays
   universal instead of the platform role becoming an exception to it.
2. **No API grants the platform role.** `TENANT_ASSIGNABLE_ROLES` is
   `("user", "admin")`; the request schemas do not accept `platform_admin` as a
   value, and `assert_role_assignable` refuses it again server-side. The first
   operator is created out of band by `scripts/bootstrap-platform-admin.sh`.
3. **Cross-tenant reads live in one place and are audited.** The tenant registry
   is `app/db/control_plane.py`, deliberately separate from `repositories.py` so
   that module keeps its "every function is tenant-filtered" contract. Every
   function there sits behind `PlatformAdminUser`, returns no tenant content, and
   records an audit event for every mutation.

A tenant admin must also never be able to lock its own company out: changing its
own role or status is refused, and so is any change that would leave the company
with no active administrator.

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

| Bucket | Limit | Keyed by |
|---|---|---|
| API requests | 20 / minute | tenant + user |
| Server-side (internal fan-out) requests | 10 / minute | tenant + user |
| Document uploads | 5 / minute | tenant + user |
| Sign-in, forgot-password, reset confirmation | 10 / minute | a hash of the account identifier |

Exceeding a limit returns `429` with a `Retry-After` header and emits a security
event.

The auth bucket is keyed by **account, not IP**, on purpose. Behind CloudFront
and an ALB the client IP is either shared by many users or supplied by the
client, so an IP-keyed limit on sign-in punishes the wrong people and is
trivially rotated around. The identifier is hashed before it reaches Redis so no
key ever contains an email address.

## 4a. Credential handling

- Passwords are Cognito's business. The application never sets, stores, reads,
  logs or returns one, and no API response contains a password field.
- New accounts are created by invitation: Cognito generates a one-time password
  and emails it directly, and the invitee replaces it on first sign-in through
  the `NEW_PASSWORD_REQUIRED` challenge. Nothing shareable ever exists.
- A failed sign-in returns one generic message for every cause. Distinguishing
  "no such user" from "wrong password" is account enumeration.
- `forgot-password` always returns `202`, whether or not the address exists.
- Deactivating a user revokes its live tokens (`AdminUserGlobalSignOut`) instead
  of letting them work until they expire.
- The ECS task role holds only the Cognito admin actions the invitation flow
  needs, on this project's own user pool ARN. `AdminSetUserPassword` and
  `AdminDeleteUser` are deliberately **not** granted: the application must not be
  able to choose someone's password or erase an identity.

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
