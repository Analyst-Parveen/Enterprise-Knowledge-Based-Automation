# Non-root, minimal, no secrets in layers.
# See .claude/rules/security.md section 6.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build deps kept out of the final image where possible.
# upgrade: the base image lags Debian security fixes (perl, glibc) that ECR flags.
# No curl: its CRITICAL CVEs have no Debian stable fix; health checks use Python.
RUN apt-get update \
 && apt-get upgrade -y \
 && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./pyproject.toml
RUN pip install --upgrade pip && pip install .

COPY backend/ /app/

# Non-root user owning only what it needs.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin ekba \
 && chown -R ekba:ekba /app
USER ekba

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
