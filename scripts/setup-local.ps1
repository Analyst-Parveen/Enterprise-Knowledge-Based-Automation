# setup-local.ps1 - One command to get the whole project running locally at $0.
#
#   .\scripts\setup-local.ps1
#
# If PowerShell blocks the script, run this once:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#
# Creates the venv, installs everything, starts the containers, runs the
# migrations, seeds demo data, and prints a login token.
# Safe to re-run: every step is idempotent.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Blue }
function Write-Ok   { param($m) Write-Host "  OK   $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host " WARN  $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host " FAIL  $m" -ForegroundColor Red; exit 1 }

Write-Host "`n Enterprise Knowledge AI - local setup" -ForegroundColor Blue
Write-Host " Everything below runs locally and costs nothing."

# ---------------------------------------------------------------------------
Write-Step "1/7  Checking prerequisites"
# ---------------------------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Fail "python not found. Install Python 3.11 or newer."
}

$pyVersion = (python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
$parts = $pyVersion.Split(".")
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
    Write-Fail "python $pyVersion is too old. This project needs 3.11 or newer."
}
Write-Ok "python $pyVersion"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Fail "docker not found. Install Docker Desktop."
}
docker info *>$null
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Docker is installed but not running. Start Docker Desktop and try again."
}
Write-Ok "docker running"

if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Ok "node $(node --version)"
} else {
    Write-Warn "node not found - the backend will still work, but the frontend will not"
}

# ---------------------------------------------------------------------------
Write-Step "2/7  Creating .env"
# ---------------------------------------------------------------------------
if (Test-Path ".env") {
    Write-Ok ".env already exists (leaving it alone)"
} else {
    Copy-Item ".env.example" ".env"

    # Generate real local secrets rather than shipping placeholders.
    $pgPass    = (python -c "import secrets; print(secrets.token_urlsafe(24))")
    $devSecret = (python -c "import secrets; print(secrets.token_urlsafe(48))")

    $content = Get-Content ".env" | ForEach-Object {
        if ($_ -like "POSTGRES_PASSWORD=*") { "POSTGRES_PASSWORD=$pgPass" }
        elseif ($_ -like "DATABASE_URL=*")  { "DATABASE_URL=postgresql+asyncpg://ekba:$pgPass@localhost:5432/ekba" }
        elseif ($_ -like "DEV_AUTH_SECRET=*") { "DEV_AUTH_SECRET=$devSecret" }
        else { $_ }
    }
    $content | Set-Content ".env" -Encoding utf8

    Write-Ok ".env created with generated local secrets"
    Write-Warn ".env is git-ignored and must never be committed"
}

# ---------------------------------------------------------------------------
Write-Step "3/7  Installing backend dependencies"
# ---------------------------------------------------------------------------
if (-not (Test-Path "backend\.venv")) {
    python -m venv backend\.venv
    Write-Ok "virtualenv created at backend\.venv"
}

$Python = Join-Path $RepoRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Fail "virtualenv looks broken. Delete backend\.venv and re-run."
}

& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet -r backend\requirements.txt -r backend\requirements-dev.txt
if ($LASTEXITCODE -ne 0) { Write-Fail "dependency install failed" }
Write-Ok "backend dependencies installed"

# ---------------------------------------------------------------------------
Write-Step "4/7  Starting containers (PostgreSQL, Qdrant, Redis, MinIO)"
# ---------------------------------------------------------------------------
# --env-file is REQUIRED: the compose file lives in infra\docker\, so without it
# Compose never reads the repo-root .env and ${POSTGRES_PASSWORD} silently falls
# back to its default - which then mismatches what the app uses.
$Compose = "infra\docker\docker-compose.yml"
$EnvFile = Join-Path $RepoRoot ".env"

# A Postgres volume keeps whatever password it was FIRST initialised with.
$volumes = docker volume ls --format "{{.Name}}"
if ($volumes -contains "ekba-dev_postgres_data") {
    $expectedPw = (Select-String -Path $EnvFile -Pattern "^POSTGRES_PASSWORD=" |
                   ForEach-Object { $_.Line.Split("=", 2)[1] })
    docker compose --env-file $EnvFile -f $Compose exec -T postgres `
        env PGPASSWORD=$expectedPw psql -U ekba -d ekba -c "SELECT 1" *>$null
    if ($LASTEXITCODE -ne 0) {
        $running = docker compose --env-file $EnvFile -f $Compose ps --status running 2>$null
        if ($running -match "postgres") {
            Write-Warn "the existing Postgres volume was created with a different password"
            Write-Warn "resetting local data (this only removes LOCAL demo data)"
            docker compose --env-file $EnvFile -f $Compose down -v *>$null
        }
    }
}

docker compose --env-file $EnvFile -f $Compose up -d postgres qdrant redis minio minio-init
if ($LASTEXITCODE -ne 0) { Write-Fail "docker compose failed" }

Write-Host "     waiting for services" -NoNewline
for ($i = 0; $i -lt 30; $i++) {
    $status = docker compose --env-file $EnvFile -f $Compose ps --format json 2>$null
    if ($status -match '"Health":"healthy"') { break }
    Write-Host "." -NoNewline
    Start-Sleep -Seconds 2
}
Write-Host ""
Write-Ok "containers up"

# ---------------------------------------------------------------------------
Write-Step "5/7  Creating database tables"
# ---------------------------------------------------------------------------
# Run from backend\ so alembic.ini and the app package are both importable.
Push-Location backend
& $Python -m alembic upgrade head
$migrateFailed = ($LASTEXITCODE -ne 0)
Pop-Location

if ($migrateFailed) {
    Write-Fail "migrations failed. Check Postgres: docker compose --env-file .env -f $Compose logs postgres"
}
Write-Ok "database schema at head"

# ---------------------------------------------------------------------------
Write-Step "6/7  Seeding demo data"
# ---------------------------------------------------------------------------
Push-Location backend
& $Python -m seeds.seed *>$null
$seedFailed = ($LASTEXITCODE -ne 0)
Pop-Location

if ($seedFailed) { Write-Fail "seeding failed" }
Write-Ok "2 tenants, 4 users, 12 documents, 8 conversations seeded"

# ---------------------------------------------------------------------------
Write-Step "7/7  Installing frontend dependencies"
# ---------------------------------------------------------------------------
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location frontend
    npm install --no-audit --no-fund --loglevel=error
    $npmFailed = ($LASTEXITCODE -ne 0)
    Pop-Location

    if ($npmFailed) { Write-Warn "npm install failed - the backend still works" }
    else { Write-Ok "frontend dependencies installed" }
} else {
    Write-Warn "npm not found - skipping the frontend"
}

# ---------------------------------------------------------------------------
# Tokens and next steps
# ---------------------------------------------------------------------------
Push-Location backend
$userToken  = (& $Python -m seeds.dev_token 2>$null)
$adminToken = (& $Python -m seeds.dev_token --role admin --user seed-admin-a 2>$null)
Pop-Location

Write-Host "`n================================================================" -ForegroundColor Green
Write-Host "  Setup complete. Nothing here costs money." -ForegroundColor Green
Write-Host "================================================================`n" -ForegroundColor Green

Write-Host "Start the backend (terminal 1):"
Write-Host "  cd backend"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  uvicorn app.main:app --reload`n"

Write-Host "Start the frontend (terminal 2):"
Write-Host "  cd frontend"
Write-Host "  npm run dev`n"

Write-Host "Then open http://localhost:3000 and paste a token:`n"
if ($userToken)  { Write-Host "  USER token" -ForegroundColor Blue; Write-Host "$userToken`n" }
if ($adminToken) { Write-Host "  ADMIN token (adds the 7 admin pages)" -ForegroundColor Blue; Write-Host "$adminToken`n" }

Write-Host "Tokens last 12 hours. Mint more with:"
Write-Host "  cd backend; python -m seeds.dev_token --role admin`n"
