# Rule: Secrets Management

## 1. Absolute prohibitions

- **Never commit** a secret, API key, token, password, private key, connection
  string with credentials, or `.env` file to git.
- **Never print** a secret to logs, console output, error messages, test output,
  CI output, or a report in `docs/reports/`.
- **Never delete** an existing secret, API key, or credential — whether or not it
  belongs to this project. Deletion of a secret requires explicit human
  instruction naming the exact secret.
- **Never overwrite** an existing secret value to "reset" it.
- **Never bake** secrets into Docker images, image layers, `ENV` instructions, or
  build arguments.
- **Never pass** secrets as command-line arguments (they appear in process lists
  and shell history).

## 2. Where secrets live

| Environment | Store |
|---|---|
| Local development | `.env` file, git-ignored, never committed |
| CI (GitHub Actions) | GitHub Encrypted Secrets / OIDC, no static AWS keys |
| AWS runtime | AWS Secrets Manager, injected at container start |

Secrets Manager secrets are tagged `Lifecycle = "protected"` and are excluded
from every destroy path. Destroying the ephemeral environment must never orphan
or delete them — a redeploy has to find them intact.

## 3. Naming

```
ekba/<env>/<component>/<name>
```

Examples: `ekba/dev/backend/database-url`,
`ekba/dev/backend/cognito-client-secret`, `ekba/dev/ai/langsmith-api-key`.

## 4. Application handling

- All configuration flows through a single typed settings object
  (`pydantic-settings`). No scattered `os.environ` reads.
- Secret-bearing fields use `SecretStr` so they do not appear in reprs, logs, or
  tracebacks.
- Never send secrets to the frontend. Anything reaching the browser is public.
  Next.js `NEXT_PUBLIC_*` variables are public by definition — never put a secret
  in one.
- Rotate by creating a new version in Secrets Manager and restarting the service.
  The application reads the current version at startup; it does not cache a value
  across a rotation without a restart or explicit refresh.

## 5. Repository hygiene

`.gitignore` must cover, at minimum:

```
.env
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
credentials
*.tfstate
*.tfstate.*
*.tfvars
!*.tfvars.example
.terraform/
```

- `.env.example` is committed and contains **placeholder values only** —
  `<replace-me>`, never a real key even for a "dead" service.
- Secret scanning runs in CI (e.g. gitleaks/trufflehog) and blocks the pipeline on
  a finding.

## 6. If a secret is exposed

1. Tell the user immediately and plainly.
2. Advise rotating the credential at its source.
3. Do **not** attempt to rewrite git history unilaterally.
4. Do **not** delete the secret from Secrets Manager as a "fix" — rotation is the
   user's call.
5. Record the incident (no secret value) in `docs/reports/`.

## 7. When in doubt

If it is unclear whether a value is a secret, treat it as one. If it is unclear
whether a secret belongs to this project, **do not touch it** and ask.
