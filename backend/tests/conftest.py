"""Shared fixtures. Unit and security tests run with no external services."""

from __future__ import annotations

import os

import pytest

# Force a hermetic dev configuration BEFORE app.core.config is imported.
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("AI_PROVIDER", "local")
os.environ.setdefault("DEV_AUTH_ENABLED", "true")
os.environ.setdefault("DEV_AUTH_SECRET", "test-secret-not-real-at-least-32-bytes-long")
os.environ.setdefault("BEDROCK_EMBEDDING_DIMENSION", "256")
# TestClient issues requests with Host: testserver. TrustedHostMiddleware
# correctly rejects unknown hosts with 400, so the test host must be allow-listed
# here rather than the middleware being weakened.
os.environ.setdefault("TRUSTED_HOSTS", "localhost,127.0.0.1,testserver")

from app.core.context import RequestContext  # noqa: E402


@pytest.fixture
def tenant_a() -> RequestContext:
    return RequestContext(
        user_id="user-a", tenant_id="tenant-a", role="user", email="a@example.com"
    )


@pytest.fixture
def tenant_b() -> RequestContext:
    return RequestContext(
        user_id="user-b", tenant_id="tenant-b", role="user", email="b@example.com"
    )


@pytest.fixture
def admin_a() -> RequestContext:
    return RequestContext(
        user_id="admin-a", tenant_id="tenant-a", role="admin", email="admin@example.com"
    )
