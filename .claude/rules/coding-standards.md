# Rule: Coding Standards

Guiding principle from PROJECT.md section 19: **minimalistic but professional.**
Every component must have a clear purpose and be explainable in an interview.
If you cannot explain why a dependency exists, it does not belong.

## 1. General

- No speculative abstraction. Build for the requirement in front of you.
- No new technology outside PROJECT.md section 2 without an explicit reason and
  user approval.
- Prefer standard library and already-present dependencies over a new package.
- Comments explain *why*, not *what*. Match the density of surrounding code.
- No dead code, no commented-out blocks, no `TODO` without an owner and context.

## 2. Backend (Python / FastAPI)

**Layering — dependencies point inward, never outward:**

```
api/v1/       thin HTTP layer: routing, dependencies, status codes
schemas/      Pydantic request/response contracts
services/     all business logic (ingestion, rag, agents, security, observability)
db/models/    SQLAlchemy models and repositories
core/         config, auth, logging, correlation ID, exceptions
workers/      async ingestion jobs
```

- Route handlers stay thin. No business logic, no direct vector or S3 calls in a
  route.
- Every request/response body is a Pydantic model. No bare `dict` in a signature.
- Full type hints. `mypy` clean.
- Format and lint with `ruff` (format + check). No unused imports or variables.
- Async I/O throughout; never block the event loop with a sync network or CPU
  call — offload to a worker or thread pool.
- Custom exception types mapped to HTTP responses in one place. Error responses
  never leak stack traces, SQL, or internal paths to the client.
- Configuration through one `Settings` object built with `pydantic-settings`.
- Every service function that touches tenant data takes an explicit tenant
  context argument. See [tenant-isolation.md](tenant-isolation.md).

**Database:**

- Every schema change ships with an Alembic migration. Never edit a migration
  that has been applied anywhere shared.
- Migrations are reversible where practical.
- No raw SQL string interpolation. Use SQLAlchemy constructs or bound parameters.
- Indexes on `tenant_id` and on every foreign key used in a filter.

## 3. Frontend (Next.js / TypeScript)

- TypeScript `strict` mode. `any` requires a comment justifying it.
- shadcn/ui components plus Tailwind. No second component library, no ad-hoc CSS
  files competing with Tailwind.
- Server Components by default; `"use client"` only where interactivity requires it.
- One typed API client module. No `fetch` calls scattered through components.
- Backend response types are mirrored in `src/types/` and kept in sync with the
  Pydantic schemas.
- Never render model output with `dangerouslySetInnerHTML` without sanitization.
- No secrets in client code. `NEXT_PUBLIC_*` values are public.
- Loading, empty, and error states are part of the definition of done for every
  data-backed view — not an afterthought.
- Accessible by default: semantic elements, labelled controls, keyboard-navigable,
  visible focus.

## 4. Naming

- Python: `snake_case` for functions/variables, `PascalCase` for classes.
- TypeScript: `camelCase` for variables/functions, `PascalCase` for components
  and types.
- AWS resources: `ekba-<env>-<component>`.
- Consistent domain vocabulary everywhere: `tenant`, `document`, `chunk`,
  `ingestion_job`, `conversation`, `message`.

## 5. Error handling and logging

- Structured JSON logs. Every log line carries the correlation ID.
- Never swallow an exception silently. Either handle it meaningfully or let it
  propagate to the central handler.
- Never log secrets, JWTs, PII, or full document contents. See
  [secrets-management.md](secrets-management.md).

## 6. Definition of done

A change is complete when:

1. It has tests (see [testing.md](testing.md)).
2. Lint, format, and type checks pass.
3. Tenant isolation is enforced and tested on any data path it touches.
4. It runs locally via Docker Compose before any AWS deployment.
5. Configuration and secrets are handled per the rules, with `.env.example`
   updated if new settings were added.
