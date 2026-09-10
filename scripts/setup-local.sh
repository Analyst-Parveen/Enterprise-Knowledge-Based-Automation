#!/usr/bin/env bash
# setup-local.sh - One command to get the whole project running locally at $0.
#
#   ./scripts/setup-local.sh
#
# Creates the venv, installs everything, starts the containers, runs the
# migrations, seeds demo data, and prints a login token.
#
# Safe to re-run: every step is idempotent.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -t 1 ]; then
  C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_RED=$'\033[31m'
  C_BLU=$'\033[34m'; C_RST=$'\033[0m'
else
  C_GRN=""; C_YEL=""; C_RED=""; C_BLU=""; C_RST=""
fi

step() { printf '\n%s==> %s%s\n' "$C_BLU" "$*" "$C_RST"; }
ok()   { printf '%s  OK  %s %s\n' "$C_GRN" "$C_RST" "$*"; }
warn() { printf '%s WARN %s %s\n' "$C_YEL" "$C_RST" "$*"; }
die()  { printf '%s FAIL %s %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }

# Windows venvs put executables in Scripts/, POSIX in bin/.
venv_python() {
  if [ -x "${REPO_ROOT}/backend/.venv/Scripts/python.exe" ]; then
    echo "${REPO_ROOT}/backend/.venv/Scripts/python.exe"
  else
    echo "${REPO_ROOT}/backend/.venv/bin/python"
  fi
}

printf '\n%s Enterprise Knowledge AI - local setup %s\n' "$C_BLU" "$C_RST"
printf ' Everything below runs locally and costs nothing.\n'

# ---------------------------------------------------------------------------
step "1/7  Checking prerequisites"
# ---------------------------------------------------------------------------
command -v python >/dev/null 2>&1 || die "python not found. Install Python 3.11 or newer."

PY_VERSION="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PY_VERSION" in
  3.1[1-9]|3.[2-9][0-9]) ok "python ${PY_VERSION}" ;;
  *) die "python ${PY_VERSION} is too old. This project needs 3.11 or newer." ;;
esac

command -v docker >/dev/null 2>&1 || die "docker not found. Install Docker Desktop."
docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop and try again."
ok "docker running"

command -v node >/dev/null 2>&1 && ok "node $(node --version)" \
  || warn "node not found - the backend will still work, but the frontend will not"

# ---------------------------------------------------------------------------
step "2/7  Creating .env"
# ---------------------------------------------------------------------------
if [ -f .env ]; then
  ok ".env already exists (leaving it alone)"
else
  cp .env.example .env

  # Generate real local secrets rather than shipping placeholders.
  PG_PASS="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
  DEV_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"

  python - "$PG_PASS" "$DEV_SECRET" <<'PYEOF'
import pathlib, sys

pg_pass, dev_secret = sys.argv[1], sys.argv[2]
path = pathlib.Path(".env")
lines = []

for line in path.read_text(encoding="utf-8").splitlines():
    if line.startswith("POSTGRES_PASSWORD="):
        line = f"POSTGRES_PASSWORD={pg_pass}"
    elif line.startswith("DATABASE_URL="):
        line = f"DATABASE_URL=postgresql+asyncpg://ekba:{pg_pass}@localhost:5432/ekba"
    elif line.startswith("DEV_AUTH_SECRET="):
        line = f"DEV_AUTH_SECRET={dev_secret}"
    lines.append(line)

path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PYEOF

  ok ".env created with generated local secrets"
  warn ".env is git-ignored and must never be committed"
fi

# ---------------------------------------------------------------------------
step "3/7  Installing backend dependencies"
# ---------------------------------------------------------------------------
if [ ! -d backend/.venv ]; then
  python -m venv backend/.venv
  ok "virtualenv created at backend/.venv"
fi

PYTHON="$(venv_python)"
[ -x "$PYTHON" ] || die "virtualenv looks broken. Delete backend/.venv and re-run."

"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet -r backend/requirements.txt -r backend/requirements-dev.txt \
  || die "dependency install failed"
ok "backend dependencies installed"

# Validate .env NOW rather than letting a typo surface later as an opaque
# SQLAlchemy or Docker error. Must run from backend/ so `app` is importable.
if ! ( cd backend && "$PYTHON" -c "from app.core.config import settings" ) >/dev/null 2>&1; then
  printf '\n'
  ( cd backend && "$PYTHON" -c "from app.core.config import settings" ) 2>&1 \
    | grep -E "Value error|Field required" | sed 's/^/  /' || true
  printf '\n'
  die ".env has a problem - see the message above. Fix it and re-run."
fi
ok ".env is valid"

# ---------------------------------------------------------------------------
step "4/7  Starting containers (PostgreSQL, Qdrant, Redis, MinIO)"
# ---------------------------------------------------------------------------
# --env-file is REQUIRED: the compose file lives in infra/docker/, so without it
# Compose never reads the repo-root .env and ${POSTGRES_PASSWORD} silently falls
# back to its default - which then mismatches what the application uses.
COMPOSE="infra/docker/docker-compose.yml"
COMPOSE_CMD=(docker compose --env-file "${REPO_ROOT}/.env" -f "$COMPOSE")

# A Postgres volume keeps whatever password it was FIRST initialised with. If
# .env now says something different, connections fail in a confusing way.
if docker volume ls --format '{{.Name}}' | grep -q '^ekba-dev_postgres_data$'; then
  EXPECTED_PW="$(grep -E '^POSTGRES_PASSWORD=' "${REPO_ROOT}/.env" | cut -d= -f2-)"
  if ! "${COMPOSE_CMD[@]}" exec -T postgres \
        env PGPASSWORD="$EXPECTED_PW" psql -U ekba -d ekba -c 'SELECT 1' >/dev/null 2>&1; then
    warn "the existing Postgres volume was created with a different password"
    warn "resetting LOCAL demo data only (nothing outside this project is touched)"
    "${COMPOSE_CMD[@]}" down -v >/dev/null 2>&1 || true
  fi
fi

"${COMPOSE_CMD[@]}" up -d postgres qdrant redis minio minio-init \
  || die "docker compose failed"

printf '     waiting for services'
for _ in $(seq 1 30); do
  if "${COMPOSE_CMD[@]}" ps --format json 2>/dev/null | grep -q '"Health":"healthy"'; then
    break
  fi
  printf '.'
  sleep 2
done
printf '\n'
ok "containers up"

# ---------------------------------------------------------------------------
step "5/7  Creating database tables"
# ---------------------------------------------------------------------------
# Run from backend/ so alembic.ini and the app package are both importable.
( cd backend && "$PYTHON" -m alembic upgrade head ) \
  || die "migrations failed. Check Postgres: docker compose --env-file .env -f ${COMPOSE} logs postgres"
ok "database schema at head"

# ---------------------------------------------------------------------------
step "6/7  Seeding demo data"
# ---------------------------------------------------------------------------
( cd backend && "$PYTHON" -m seeds.seed >/dev/null 2>&1 ) \
  || die "seeding failed"
ok "2 tenants, 4 users, 12 documents, 8 conversations seeded"

# ---------------------------------------------------------------------------
step "7/7  Installing frontend dependencies"
# ---------------------------------------------------------------------------
if command -v npm >/dev/null 2>&1; then
  ( cd frontend && npm install --no-audit --no-fund --loglevel=error ) \
    || warn "npm install failed - the backend still works"
  ok "frontend dependencies installed"
else
  warn "npm not found - skipping the frontend"
fi

# ---------------------------------------------------------------------------
# Tokens and next steps
# ---------------------------------------------------------------------------
USER_TOKEN="$( cd backend && "$PYTHON" -m seeds.dev_token 2>/dev/null || echo "" )"
ADMIN_TOKEN="$( cd backend && "$PYTHON" -m seeds.dev_token --role admin --user seed-admin-a 2>/dev/null || echo "" )"

printf '\n%s================================================================%s\n' "$C_GRN" "$C_RST"
printf '%s  Setup complete. Nothing here costs money.%s\n' "$C_GRN" "$C_RST"
printf '%s================================================================%s\n\n' "$C_GRN" "$C_RST"

printf 'Start the backend (terminal 1):\n'
printf '  cd backend\n'
if [ -x "${REPO_ROOT}/backend/.venv/Scripts/python.exe" ]; then
  printf '  source .venv/Scripts/activate\n'
else
  printf '  source .venv/bin/activate\n'
fi
printf '  uvicorn app.main:app --reload\n\n'

printf 'Start the frontend (terminal 2):\n'
printf '  cd frontend\n'
printf '  npm run dev\n\n'

printf 'Then open http://localhost:3000 and paste a token:\n\n'
if [ -n "$USER_TOKEN" ]; then
  printf '%s  USER token%s\n%s\n\n' "$C_BLU" "$C_RST" "$USER_TOKEN"
fi
if [ -n "$ADMIN_TOKEN" ]; then
  printf '%s  ADMIN token%s (adds the 7 admin pages)\n%s\n\n' "$C_BLU" "$C_RST" "$ADMIN_TOKEN"
fi
printf 'Tokens last 12 hours. Mint more with:\n'
printf '  cd backend && python -m seeds.dev_token --role admin\n\n'
