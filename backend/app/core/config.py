"""Typed application settings.

All configuration flows through this one object - no scattered os.environ reads.
Secret-bearing fields use SecretStr so they never appear in reprs or tracebacks.
See .claude/rules/secrets-management.md section 4.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "staging", "prod"]
AIProvider = Literal["bedrock", "local"]

# app/core/config.py -> core -> app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Look for .env at the repo root FIRST, then in the current directory.
        # Without the absolute path, running `alembic` from backend/ would miss
        # the root .env entirely and silently fall back to these defaults - which
        # then fail against a container started with different credentials.
        # Later entries win, so a local backend/.env can still override.
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- project ---------------------------------------------------------
    environment: Environment = "dev"
    project_code: str = "ekba"
    log_level: str = "INFO"

    # -- cost control ($20 ceiling) --------------------------------------
    max_monthly_spend_usd: float = 20.0
    daily_cost_ceiling_usd: float = 0.50

    # -- datastores ------------------------------------------------------
    database_url: str = "postgresql+asyncpg://ekba:ekba@localhost:5432/ekba"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "ekba_chunks"

    # -- storage ---------------------------------------------------------
    s3_bucket: str = "ekba-dev-documents"
    s3_endpoint_url: str | None = None  # set for local MinIO, unset on AWS
    s3_upload_max_bytes: int = 52_428_800  # 50 MB

    # -- auth (Amazon Cognito) -------------------------------------------
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_region: str = "us-west-2"

    # Local development without a Cognito pool. Hard-gated: see auth.py.
    # NEVER enable outside dev - a test asserts this.
    dev_auth_enabled: bool = False
    dev_auth_secret: SecretStr = SecretStr("dev-only-not-a-real-secret")

    # -- AI: Amazon Bedrock ----------------------------------------------
    ai_provider: AIProvider = "bedrock"
    aws_region: str = "us-west-2"
    bedrock_region: str = "us-west-2"
    transcribe_region: str = "us-west-2"

    bedrock_chat_primary_model_id: str = "us.amazon.nova-lite-v1:0"
    bedrock_chat_fallback_model_id: str = "us.amazon.nova-micro-v1:0"
    bedrock_vision_model_id: str = "us.amazon.nova-lite-v1:0"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    bedrock_embedding_dimension: int = 1024

    # -- RAG / guardrail tuning ------------------------------------------
    retrieval_top_k: int = 8
    relevance_threshold: float = 0.35
    # The local dev provider uses hashed bag-of-words vectors, whose cosine
    # scores sit far below a real embedding model's. Measured against the golden
    # set, correct documents score 0.22-0.70 locally versus 0.6-0.8 on Titan, so
    # the Bedrock-tuned threshold above filters out genuine matches and the demo
    # answers "not found" to everything. Applied only when AI_PROVIDER=local.
    local_relevance_threshold: float = 0.15
    max_output_tokens: int = 1024
    max_input_tokens: int = 8192
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64
    semantic_cache_ttl_seconds: int = 3600
    semantic_cache_similarity: float = 0.95

    # -- rate limits (per user per minute) -------------------------------
    rate_limit_requests_per_min: int = 20
    rate_limit_server_requests_per_min: int = 10
    rate_limit_uploads_per_min: int = 5

    # -- security --------------------------------------------------------
    cors_allowed_origins: str = "http://localhost:3000"
    trusted_hosts: str = "localhost,127.0.0.1"
    max_request_bytes: int = 1_048_576  # 1 MB for non-upload bodies

    # -- observability ---------------------------------------------------
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "ekba-dev"
    langsmith_tracing: bool = False

    @field_validator("database_url", mode="before")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        """Catch the common .env mistakes with a message that names the problem.

        SQLAlchemy's own error for these is an opaque "Could not parse URL",
        which sends people hunting in the wrong place.
        """
        if not isinstance(v, str):
            return v
        url = v.strip().strip('"').strip("'")

        if not url:
            raise ValueError("DATABASE_URL is empty. Set it in .env.")

        if url.startswith("="):
            raise ValueError(
                "DATABASE_URL starts with '=' - you have a double equals in .env. "
                "It should read DATABASE_URL=postgresql+asyncpg://... (one '=')."
            )

        if "://" not in url:
            raise ValueError(
                f"DATABASE_URL is not a URL: {url[:30]!r}. "
                "Expected postgresql+asyncpg://user:password@host:5432/dbname"
            )

        # This app uses an async engine, which needs the asyncpg driver.
        # A bare postgresql:// otherwise fails much later with a confusing error.
        scheme = url.split("://", 1)[0]
        if scheme == "postgresql":
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif scheme not in ("postgresql+asyncpg", "sqlite+aiosqlite"):
            raise ValueError(
                f"DATABASE_URL uses the {scheme!r} driver, which is not async. "
                "Use postgresql+asyncpg://"
            )

        return url

    @field_validator("cors_allowed_origins", "trusted_hosts")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty - an empty allow-list is not a wildcard")
        return v

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()]

    @property
    def is_dev(self) -> bool:
        return self.environment == "dev"

    @property
    def effective_relevance_threshold(self) -> float:
        """Relevance cutoff matched to the embedding model actually in use.

        A threshold is a property of the embedding model's score distribution,
        not a universal constant - so it moves with the provider rather than
        being tuned to whichever one happens to be configured.
        """
        if self.ai_provider == "local":
            return self.local_relevance_threshold
        return self.relevance_threshold

    @property
    def cognito_issuer(self) -> str:
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"
        )

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
