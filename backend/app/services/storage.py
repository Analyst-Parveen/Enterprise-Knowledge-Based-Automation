"""S3 document storage.

Keys are always <tenant_id>/<uuid>.<ext>, generated server-side. Access is
mediated here - no direct client access to arbitrary keys, and every read
verifies the key belongs to the caller's tenant.

See .claude/rules/tenant-isolation.md section 6.
"""

from __future__ import annotations

import asyncio

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import settings
from app.core.context import RequestContext
from app.core.exceptions import NotFoundError, TenantIsolationError, UpstreamError
from app.core.logging import get_logger, log_security_event

logger = get_logger(__name__)

_client = None


def _s3():  # type: ignore[no-untyped-def]
    global _client
    if _client is None:
        cfg = BotoConfig(
            region_name=settings.aws_region,
            retries={"max_attempts": 3, "mode": "standard"},
            signature_version="s3v4",
        )
        kwargs = {"config": cfg}
        if settings.s3_endpoint_url:  # local MinIO
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        _client = boto3.client("s3", **kwargs)
    return _client


def assert_key_in_tenant(ctx: RequestContext, key: str) -> None:
    """A key must live under the caller's tenant prefix. No exceptions."""
    prefix = f"{ctx.tenant_id}/"
    if not key.startswith(prefix):
        log_security_event(
            "tenant.cross_tenant_storage_access",
            reason="s3_key_outside_tenant_prefix",
            severity="critical",
        )
        raise TenantIsolationError()


async def put_object(key: str, data: bytes, content_type: str) -> str:
    """Upload with server-side encryption. Returns the s3:// URI."""

    def _put() -> None:
        _s3().put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )

    try:
        await asyncio.to_thread(_put)
    except (ClientError, BotoCoreError) as exc:
        logger.error("s3_put_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("Storage is unavailable.") from exc

    return f"s3://{settings.s3_bucket}/{key}"


async def get_object(ctx: RequestContext, key: str) -> bytes:
    assert_key_in_tenant(ctx, key)

    def _get() -> bytes:
        response = _s3().get_object(Bucket=settings.s3_bucket, Key=key)
        return bytes(response["Body"].read())

    try:
        return await asyncio.to_thread(_get)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            raise NotFoundError("Document content not found.") from exc
        logger.error("s3_get_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("Storage is unavailable.") from exc
    except BotoCoreError as exc:
        raise UpstreamError("Storage is unavailable.") from exc


async def delete_object(ctx: RequestContext, key: str) -> None:
    assert_key_in_tenant(ctx, key)

    def _delete() -> None:
        _s3().delete_object(Bucket=settings.s3_bucket, Key=key)

    try:
        await asyncio.to_thread(_delete)
    except (ClientError, BotoCoreError) as exc:
        logger.error("s3_delete_failed", extra={"extra": {"error": type(exc).__name__}})
        raise UpstreamError("Storage is unavailable.") from exc


async def presigned_url(ctx: RequestContext, key: str, expires_in: int = 300) -> str:
    """Short-lived download URL, issued only after the tenant check."""
    assert_key_in_tenant(ctx, key)

    def _sign() -> str:
        return str(
            _s3().generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.s3_bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        )

    try:
        return await asyncio.to_thread(_sign)
    except (ClientError, BotoCoreError) as exc:
        raise UpstreamError("Storage is unavailable.") from exc


def key_from_uri(source_uri: str) -> str:
    """s3://bucket/key -> key"""
    if source_uri.startswith("s3://"):
        return source_uri.split("/", 3)[3]
    return source_uri
