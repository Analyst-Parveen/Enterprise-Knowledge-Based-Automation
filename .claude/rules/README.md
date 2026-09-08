# Project Rules

These files are **binding constraints** on all work in this repository. They are
not suggestions. When a rule conflicts with a convenient shortcut, the rule wins.

Architecture and requirements live in [PROJECT.md](../../PROJECT.md).
Operational workflows live in [.claude/skills/](../skills/).

| Rule | Covers |
|---|---|
| [security.md](security.md) | Threat model, authn/authz, transport, files, headers, rate limits |
| [guardrails.md](guardrails.md) | Prompt injection, output filtering, citation validation, refusals |
| [tenant-isolation.md](tenant-isolation.md) | The single most important invariant in this project |
| [ai-model-usage.md](ai-model-usage.md) | Bedrock, model routing, embeddings, cost and token discipline |
| [terraform.md](terraform.md) | State, tagging, plan/apply discipline, destroy restrictions |
| [aws-infrastructure.md](aws-infrastructure.md) | Account/region safety, ownership verification, cost strategy |
| [secrets-management.md](secrets-management.md) | Secrets Manager, protected secrets, never-commit rules |
| [coding-standards.md](coding-standards.md) | Backend and frontend conventions |
| [testing.md](testing.md) | Pytest, Playwright, security tests, evaluation gates |
| [deployment.md](deployment.md) | Lifecycle, blue-green, health checks, rollback |

## Precedence

1. **Safety rules** (destructive actions, secrets, cross-tenant, unrelated infra)
2. **Security and tenant isolation rules**
3. **PROJECT.md architecture**
4. **Everything else**

A lower-precedence rule never authorizes violating a higher one.
