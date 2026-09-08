"""Upload safety: extension allow-list, magic-byte sniffing, size caps, safe keys.

Order matters (see .claude/skills/document-ingestion/SKILL.md step 1):
  1. extension allow-list
  2. magic bytes must AGREE with the declared type
  3. size cap enforced while streaming, before the whole body is in memory
  4. S3 key generated server-side from tenant_id + uuid, NEVER the filename
"""

from __future__ import annotations

import hashlib
import re
import uuid

from app.core.exceptions import PayloadTooLargeError, UnsupportedMediaTypeError
from app.core.logging import log_security_event
from app.db.models import Modality

# extension -> (canonical content type, modality)
ALLOWED_EXTENSIONS: dict[str, tuple[str, Modality]] = {
    # text
    "pdf": ("application/pdf", Modality.TEXT),
    "txt": ("text/plain", Modality.TEXT),
    "md": ("text/markdown", Modality.TEXT),
    "doc": ("application/msword", Modality.TEXT),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        Modality.TEXT,
    ),
    # tabular
    "csv": ("text/csv", Modality.TABLE),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        Modality.TABLE,
    ),
    "xls": ("application/vnd.ms-excel", Modality.TABLE),
    # images
    "png": ("image/png", Modality.IMAGE),
    "jpg": ("image/jpeg", Modality.IMAGE),
    "jpeg": ("image/jpeg", Modality.IMAGE),
    "webp": ("image/webp", Modality.IMAGE),
    "gif": ("image/gif", Modality.IMAGE),
    # audio
    "mp3": ("audio/mpeg", Modality.AUDIO),
    "wav": ("audio/wav", Modality.AUDIO),
    "m4a": ("audio/mp4", Modality.AUDIO),
    "flac": ("audio/flac", Modality.AUDIO),
    "ogg": ("audio/ogg", Modality.AUDIO),
    # video (Transcribe reads the audio track directly - no ffmpeg needed)
    "mp4": ("video/mp4", Modality.VIDEO),
    "mov": ("video/quicktime", Modality.VIDEO),
    "webm": ("video/webm", Modality.VIDEO),
}

# Magic-byte signatures: (offset, signature) alternatives per extension.
_SIGNATURES: dict[str, list[tuple[int, bytes]]] = {
    "pdf": [(0, b"%PDF-")],
    "png": [(0, b"\x89PNG\r\n\x1a\n")],
    "jpg": [(0, b"\xff\xd8\xff")],
    "jpeg": [(0, b"\xff\xd8\xff")],
    "gif": [(0, b"GIF87a"), (0, b"GIF89a")],
    "webp": [(8, b"WEBP")],
    # OOXML and modern Office formats are ZIP containers
    "docx": [(0, b"PK\x03\x04")],
    "xlsx": [(0, b"PK\x03\x04")],
    "doc": [(0, b"\xd0\xcf\x11\xe0")],  # OLE2 compound file
    "xls": [(0, b"\xd0\xcf\x11\xe0")],
    "mp3": [(0, b"ID3"), (0, b"\xff\xfb"), (0, b"\xff\xf3"), (0, b"\xff\xf2")],
    "wav": [(0, b"RIFF")],
    "flac": [(0, b"fLaC")],
    "ogg": [(0, b"OggS")],
    "m4a": [(4, b"ftyp")],
    "mp4": [(4, b"ftyp")],
    "mov": [(4, b"ftyp")],
    "webm": [(0, b"\x1a\x45\xdf\xa3")],
}

# Text formats have no reliable magic bytes; validated by decodability instead.
_TEXT_LIKE = {"txt", "md", "csv"}

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")
MAX_FILENAME_LENGTH = 255


def extract_extension(filename: str) -> str:
    """Take the last extension only. Rejects traversal and null bytes."""
    if "\x00" in filename:
        log_security_event(
            "upload.null_byte_filename", reason="null_byte_in_filename", severity="error"
        )
        raise UnsupportedMediaTypeError("Invalid filename.")

    # Traversal is checked on the ORIGINAL string, before any stripping - a
    # legitimate upload never contains "..", so its presence is an attack signal
    # worth rejecting and auditing rather than silently normalizing away.
    if ".." in filename:
        log_security_event(
            "upload.path_traversal_filename", reason="dotdot_in_filename", severity="error"
        )
        raise UnsupportedMediaTypeError("Invalid filename.")

    # Plain directory components (browsers have historically sent full paths)
    # are stripped rather than rejected.
    base = filename.replace("\\", "/").split("/")[-1]

    if "." not in base:
        raise UnsupportedMediaTypeError("File has no extension.")
    return base.rsplit(".", 1)[-1].lower()


def sanitize_display_name(filename: str) -> str:
    """The original name is metadata only - never used to build a path."""
    base = filename.replace("\\", "/").split("/")[-1]
    cleaned = _SAFE_NAME_RE.sub("_", base).strip() or "document"
    return cleaned[:MAX_FILENAME_LENGTH]


def validate_extension(filename: str) -> tuple[str, str, Modality]:
    ext = extract_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        log_security_event("upload.disallowed_extension", reason=f"ext_{ext}_not_allowed")
        raise UnsupportedMediaTypeError(f"Files of type .{ext} are not accepted.")
    content_type, modality = ALLOWED_EXTENSIONS[ext]
    return ext, content_type, modality


def verify_magic_bytes(ext: str, head: bytes) -> None:
    """The actual bytes must agree with the declared extension."""
    if ext in _TEXT_LIKE:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            try:
                head.decode("latin-1")
            except UnicodeDecodeError as exc:
                log_security_event(
                    "upload.magic_mismatch", reason=f"{ext}_not_decodable", severity="error"
                )
                raise UnsupportedMediaTypeError("File content does not match its type.") from exc
        return

    signatures = _SIGNATURES.get(ext)
    if not signatures:
        return

    for offset, signature in signatures:
        if head[offset : offset + len(signature)] == signature:
            return

    log_security_event(
        "upload.magic_mismatch", reason=f"{ext}_signature_mismatch", severity="error"
    )
    raise UnsupportedMediaTypeError("File content does not match its declared type.")


def enforce_size(size_bytes: int, max_bytes: int) -> None:
    if size_bytes > max_bytes:
        log_security_event("upload.too_large", reason=f"size_{size_bytes}_over_{max_bytes}")
        raise PayloadTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")


def build_storage_key(tenant_id: str, ext: str) -> str:
    """S3 key is <tenant_id>/<uuid>.<ext> - derived from the token, not the client.

    This is what makes path traversal structurally impossible.
    """
    safe_tenant = _SAFE_NAME_RE.sub("_", tenant_id)
    return f"{safe_tenant}/{uuid.uuid4().hex}.{ext}"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
