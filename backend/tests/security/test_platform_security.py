"""Platform security: auth, RBAC, upload safety, dev-auth gating."""

from __future__ import annotations

import pytest

from app.core.context import RequestContext
from app.core.exceptions import (
    AuthorizationError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.services.security import files


class TestUploadSafety:
    def test_disallowed_extension_rejected(self) -> None:
        for name in ("evil.exe", "script.sh", "payload.bat", "lib.dll"):
            with pytest.raises(UnsupportedMediaTypeError):
                files.validate_extension(name)

    def test_allowed_extensions_accepted(self) -> None:
        for name in ("policy.pdf", "notes.md", "data.csv", "sheet.xlsx", "clip.mp3"):
            ext, content_type, modality = files.validate_extension(name)
            assert ext and content_type and modality

    def test_path_traversal_filename_rejected(self) -> None:
        for name in ("../../etc/passwd.pdf", "..\\..\\windows\\system.pdf"):
            with pytest.raises(UnsupportedMediaTypeError):
                files.extract_extension(name)

    def test_null_byte_filename_rejected(self) -> None:
        with pytest.raises(UnsupportedMediaTypeError):
            files.extract_extension("evil\x00.pdf")

    def test_directory_component_stripped(self) -> None:
        assert files.extract_extension("/var/www/uploads/report.pdf") == "pdf"
        assert files.extract_extension("C:\\Users\\bob\\report.pdf") == "pdf"

    def test_double_extension_takes_the_last(self) -> None:
        """'evil.pdf.exe' is an exe, and must be rejected as one."""
        with pytest.raises(UnsupportedMediaTypeError):
            files.validate_extension("evil.pdf.exe")

    def test_magic_bytes_must_match_extension(self) -> None:
        # A PE executable renamed to .pdf
        with pytest.raises(UnsupportedMediaTypeError):
            files.verify_magic_bytes("pdf", b"MZ\x90\x00\x03\x00\x00\x00")

    def test_correct_magic_bytes_accepted(self) -> None:
        files.verify_magic_bytes("pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3")
        files.verify_magic_bytes("png", b"\x89PNG\r\n\x1a\n\x00\x00")
        files.verify_magic_bytes("docx", b"PK\x03\x04\x14\x00")

    def test_size_cap_enforced(self) -> None:
        files.enforce_size(1000, 2000)  # under - fine
        with pytest.raises(PayloadTooLargeError):
            files.enforce_size(3000, 2000)

    def test_storage_key_ignores_user_filename(self) -> None:
        """The key must be uuid-based, carrying nothing from the client."""
        key = files.build_storage_key("tenant-a", "pdf")
        assert key.startswith("tenant-a/")
        assert key.endswith(".pdf")
        assert "passwd" not in key and ".." not in key

    def test_display_name_sanitized(self) -> None:
        cleaned = files.sanitize_display_name("../../etc/pa$$wd;rm -rf.pdf")
        assert ".." not in cleaned
        assert "/" not in cleaned and "\\" not in cleaned
        assert ";" not in cleaned


class TestRBAC:
    def test_non_admin_denied_admin_gate(self, tenant_a: RequestContext) -> None:
        from app.core.auth import require_admin

        with pytest.raises(AuthorizationError):
            require_admin(tenant_a)

    def test_admin_allowed(self, admin_a: RequestContext) -> None:
        from app.core.auth import require_admin

        require_admin(admin_a)  # must not raise

    def test_roles_are_exactly_user_and_admin(self) -> None:
        from app.db.models import UserRole

        assert {r.value for r in UserRole} == {"user", "admin"}


class TestDevAuthGating:
    """Dev auth must be unreachable outside local development."""

    def test_dev_token_rejected_when_not_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core import auth
        from app.core.config import settings
        from app.core.exceptions import AuthenticationError

        monkeypatch.setattr(settings, "environment", "prod")
        with pytest.raises(AuthenticationError):
            auth._verify_dev_token("any.token.here")

    def test_dev_token_rejected_when_flag_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core import auth
        from app.core.config import settings
        from app.core.exceptions import AuthenticationError

        monkeypatch.setattr(settings, "dev_auth_enabled", False)
        with pytest.raises(AuthenticationError):
            auth._verify_dev_token("any.token.here")

    def test_local_ai_provider_refused_outside_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.config import settings
        from app.services.ai import provider

        provider.reset_provider()
        monkeypatch.setattr(settings, "environment", "prod")
        monkeypatch.setattr(settings, "ai_provider", "local")
        with pytest.raises(RuntimeError, match="dev"):
            provider.get_provider()
        provider.reset_provider()


class TestTokenClaims:
    def test_token_without_tenant_rejected(self) -> None:
        from app.core.auth import _claims_to_context
        from app.core.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError, match="tenant"):
            _claims_to_context({"sub": "user-1", "custom:role": "user"})

    def test_unknown_role_rejected(self) -> None:
        from app.core.auth import _claims_to_context
        from app.core.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError):
            _claims_to_context({"sub": "u1", "custom:tenant_id": "t1", "custom:role": "superuser"})

    def test_valid_claims_build_context(self) -> None:
        from app.core.auth import _claims_to_context

        ctx = _claims_to_context(
            {"sub": "u1", "custom:tenant_id": "t1", "custom:role": "admin", "email": "a@b.c"}
        )
        assert ctx.tenant_id == "t1"
        assert ctx.is_admin


class TestSecurityHeaders:
    def test_required_headers_configured(self) -> None:
        from app.core.middleware import SECURITY_HEADERS

        for header in (
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Content-Security-Policy",
        ):
            assert header in SECURITY_HEADERS

    def test_cors_is_never_wildcard(self) -> None:
        from app.core.config import settings

        assert "*" not in settings.cors_origins


class TestRateLimitConfig:
    def test_limits_match_project_spec(self) -> None:
        """PROJECT.md section 9: 20 / 10 / 5 per minute per user."""
        from app.core.config import settings

        assert settings.rate_limit_requests_per_min == 20
        assert settings.rate_limit_server_requests_per_min == 10
        assert settings.rate_limit_uploads_per_min == 5
