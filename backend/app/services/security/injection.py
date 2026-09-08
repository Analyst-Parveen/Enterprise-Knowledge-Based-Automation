"""Prompt-injection detection for user input and for ingested document content.

Two call sites, same engine:
  * scan_input()   - the user's question, before retrieval. Blocks.
  * scan_content() - extracted document text, at ingestion. Flags, quarantines.

Retrieved chunks are DATA, never instructions. This module is the second line of
defence; the first is the data envelope in rag/prompts.py.

See .claude/rules/guardrails.md sections 2 and 3.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from app.core.logging import log_security_event


class Severity(str, Enum):
    NONE = "none"
    SUSPICIOUS = "suspicious"
    MALICIOUS = "malicious"


@dataclass(slots=True)
class ScanResult:
    severity: Severity
    reasons: list[str]

    @property
    def is_blocking(self) -> bool:
        return self.severity is Severity.MALICIOUS

    @property
    def is_clean(self) -> bool:
        return self.severity is Severity.NONE


# Patterns are reason-coded so logs never need the offending payload.
_MALICIOUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b[\s\S]{0,40}?"
            r"\b(previous|prior|above|earlier|all|any)\b[\s\S]{0,30}?"
            r"\b(instruction|prompt|rule|direction|context|command)s?\b",
            re.I,
        ),
    ),
    (
        "system_prompt_extraction",
        re.compile(
            r"\b(show|print|reveal|repeat|output|display|tell\s+me|what\s+(is|are|was))\b"
            r"[\s\S]{0,40}?\b(system\s*prompt|initial\s*(instruction|prompt)|"
            r"your\s+(instruction|rule|prompt|directive)s?|everything\s+above)\b",
            re.I,
        ),
    ),
    (
        "role_manipulation",
        re.compile(
            r"\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be|roleplay\s+as|"
            r"from\s+now\s+on\s+you|new\s+persona|developer\s+mode|DAN\s+mode)\b",
            re.I,
        ),
    ),
    (
        "guardrail_bypass",
        re.compile(
            r"\b(bypass|disable|turn\s+off|circumvent|ignore)\b[\s\S]{0,30}?"
            r"\b(guardrail|safety|filter|restriction|security|policy|limitation)s?\b",
            re.I,
        ),
    ),
    (
        "exfiltration",
        re.compile(
            r"\b(send|post|upload|exfiltrate|transmit|leak|forward)\b[\s\S]{0,40}?"
            r"\b(to\s+https?://|to\s+the\s+following\s+(url|address|endpoint)|"
            r"webhook|external\s+server)\b",
            re.I,
        ),
    ),
    (
        "delimiter_injection",
        re.compile(
            r"(</?(system|assistant|instruction|context)>|"
            r"\[\s*/?\s*(SYSTEM|INST|INSTRUCTION)\s*\]|"
            r"###\s*(system|instruction)\s*:)",
            re.I,
        ),
    ),
    (
        "credential_probe",
        re.compile(
            r"\b(api[_\-\s]?key|secret[_\-\s]?key|password|access[_\-\s]?token|"
            r"credential|private[_\-\s]?key)\b[\s\S]{0,30}?"
            r"\b(what|show|give|reveal|print|list|tell)\b",
            re.I,
        ),
    ),
]

_SUSPICIOUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "embedded_directive",
        re.compile(
            r"\b(important|note|attention|urgent)\s*[:!]\s*"
            r"(the\s+)?(assistant|ai|model|system|you)\s+(must|should|will|shall)\b",
            re.I,
        ),
    ),
    (
        "hidden_instruction_marker",
        re.compile(r"\b(hidden|invisible|secret)\s+(instruction|message|command|prompt)\b", re.I),
    ),
    (
        "prompt_leak_bait",
        re.compile(
            r"\b(verbatim|word\s+for\s+word|exactly\s+as\s+written)\b[\s\S]{0,30}?"
            r"\b(above|prior|preceding|prompt)\b",
            re.I,
        ),
    ),
]

# Zero-width and bidi control characters used to smuggle hidden instructions.
_INVISIBLE_CHARS = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")

_BASE64_BLOB = re.compile(r"\b[A-Za-z0-9+/]{80,}={0,2}\b")
_HEX_BLOB = re.compile(r"\b(?:[0-9a-fA-F]{2}){40,}\b")


def normalize(text: str) -> str:
    """Defeat homoglyph and zero-width evasion before pattern matching."""
    text = unicodedata.normalize("NFKC", text)
    return _INVISIBLE_CHARS.sub("", text)


def _decoded_candidates(text: str) -> list[str]:
    """Decode base64 blobs so payloads hidden inside them are still scanned."""
    candidates: list[str] = []
    for match in _BASE64_BLOB.findall(text)[:5]:
        try:
            decoded = base64.b64decode(match, validate=True).decode("utf-8", errors="ignore")
        except (binascii.Error, ValueError):
            continue
        if decoded.isprintable() or " " in decoded:
            candidates.append(decoded)
    return candidates


def _scan(text: str) -> ScanResult:
    normalized = normalize(text)
    reasons: list[str] = []
    severity = Severity.NONE

    if _INVISIBLE_CHARS.search(text):
        reasons.append("invisible_characters")
        severity = Severity.SUSPICIOUS

    haystacks = [normalized, *_decoded_candidates(normalized)]

    for haystack in haystacks:
        for reason, pattern in _MALICIOUS_PATTERNS:
            if pattern.search(haystack) and reason not in reasons:
                reasons.append(reason)
                severity = Severity.MALICIOUS
        for reason, pattern in _SUSPICIOUS_PATTERNS:
            if pattern.search(haystack) and reason not in reasons:
                reasons.append(reason)
                if severity is Severity.NONE:
                    severity = Severity.SUSPICIOUS

    # A very long opaque blob in a question is itself odd.
    if _HEX_BLOB.search(normalized) and severity is Severity.NONE:
        reasons.append("opaque_encoded_blob")
        severity = Severity.SUSPICIOUS

    return ScanResult(severity=severity, reasons=reasons)


def scan_input(question: str) -> ScanResult:
    """Scan the user's question. MALICIOUS results are blocked by the caller."""
    result = _scan(question)
    if result.severity is not Severity.NONE:
        log_security_event(
            "guardrail.input_injection_detected",
            reason=",".join(result.reasons),
            severity="error" if result.is_blocking else "warning",
            stage="input_scan",
        )
    return result


def scan_content(text: str) -> ScanResult:
    """Scan extracted document text at ingestion time.

    Malicious content is flagged so the chunk is quarantined from retrieval
    (the `suspicious` payload field) rather than silently indexed.
    """
    result = _scan(text)
    if result.is_blocking:
        log_security_event(
            "guardrail.retrieval_poisoning_detected",
            reason=",".join(result.reasons),
            severity="error",
            stage="ingestion_scan",
        )
    return result


def validate_question(question: str, max_length: int = 4000) -> str:
    """Input validation: length, emptiness, control characters."""
    from app.core.exceptions import ValidationError

    stripped = question.strip()
    if not stripped:
        raise ValidationError("The question is empty.")
    if len(stripped) > max_length:
        raise ValidationError(f"The question exceeds {max_length} characters.")

    # Allow tab/newline, reject other C0 control characters.
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in stripped):
        log_security_event("guardrail.control_characters", reason="control_chars_in_input")
        raise ValidationError("The question contains invalid characters.")

    return stripped
