"""Output-side guardrails: citation validation, output filtering, confidence.

Applied in order before any response leaves the service:
  1. citation validation - citations must map to chunks actually retrieved
  2. output guardrail    - block system-prompt leakage, secrets, unsafe markup
  3. confidence          - derived from retrieval scores and citation coverage

See .claude/rules/guardrails.md section 5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.logging import log_security_event
from app.services.rag.prompts import BuiltContext
from app.services.vector import SearchHit

_CITATION_RE = re.compile(r"\[S(\d{1,2})\]")

# Unsafe markup that must never reach the browser from model output.
_UNSAFE_MARKUP = [
    ("script_tag", re.compile(r"<\s*script\b", re.I)),
    ("iframe_tag", re.compile(r"<\s*iframe\b", re.I)),
    ("event_handler", re.compile(r"\bon(click|error|load|mouseover|focus)\s*=", re.I)),
    ("js_uri", re.compile(r"javascript\s*:", re.I)),
    ("data_html_uri", re.compile(r"data:text/html", re.I)),
]

# System-prompt leakage indicators.
_PROMPT_LEAK = [
    (
        "system_prompt_echo",
        re.compile(r"You are the knowledge assistant for a private enterprise", re.I),
    ),
    ("rule_list_echo", re.compile(r"Rules you always follow\s*:", re.I)),
    ("envelope_echo", re.compile(r"<<<(BEGIN|END)_SOURCES>>>", re.I)),
]

# Credential shapes that must never be echoed even if present in a document.
_SECRETS = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")),
]


@dataclass(slots=True)
class Citation:
    source_number: int
    document_id: str
    document_name: str
    chunk_id: str
    page_number: int | None = None
    section: str | None = None
    score: float = 0.0


@dataclass(slots=True)
class GuardedOutput:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None
    invented_citations: int = 0
    confidence: float = 0.0


def validate_citations(
    answer: str, context: BuiltContext, tenant_id: str
) -> tuple[str, list[Citation], int]:
    """Keep only citations that map to chunks actually retrieved for THIS request.

    An invented citation is removed from the answer text and counted - it lowers
    confidence rather than silently passing as fact.
    """
    valid_by_number: dict[int, SearchHit] = {
        number: hit
        for hit in context.used_hits
        for number in [context.source_labels.get(str(hit.payload.get("chunk_id", "")), 0)]
        if number
    }

    cited_numbers = {int(n) for n in _CITATION_RE.findall(answer)}
    invented = sorted(n for n in cited_numbers if n not in valid_by_number)

    cleaned = answer
    for number in invented:
        cleaned = cleaned.replace(f"[S{number}]", "")
    if invented:
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        log_security_event(
            "guardrail.invented_citation",
            reason=f"unmapped_source_numbers_{len(invented)}",
            stage="citation_validation",
        )

    citations: list[Citation] = []
    for number in sorted(cited_numbers & valid_by_number.keys()):
        hit = valid_by_number[number]
        payload = hit.payload

        # Defence in depth: a citation may only reference this tenant's document.
        if payload.get("tenant_id") != tenant_id:
            log_security_event(
                "tenant.citation_cross_tenant",
                reason="citation_referenced_foreign_tenant",
                severity="critical",
            )
            continue

        citations.append(
            Citation(
                source_number=number,
                document_id=str(payload.get("document_id", "")),
                document_name=str(payload.get("document_name", "")),
                chunk_id=str(payload.get("chunk_id", "")),
                page_number=payload.get("page_number"),
                section=payload.get("section"),
                score=hit.score,
            )
        )

    return cleaned, citations, len(invented)


def apply_output_guardrail(answer: str) -> tuple[str, str | None]:
    """Block system-prompt leakage, credentials, and unsafe markup."""
    for reason, pattern in _PROMPT_LEAK:
        if pattern.search(answer):
            log_security_event(
                "guardrail.system_prompt_leak_blocked",
                reason=reason,
                severity="error",
                stage="output_guardrail",
            )
            return (
                "I can't share details about my configuration. "
                "Ask me about your knowledge base instead.",
                reason,
            )

    for reason, pattern in _SECRETS:
        if pattern.search(answer):
            log_security_event(
                "guardrail.secret_in_output_blocked",
                reason=reason,
                severity="critical",
                stage="output_guardrail",
            )
            return (
                "The answer was withheld because it appeared to contain credentials.",
                reason,
            )

    for reason, pattern in _UNSAFE_MARKUP:
        if pattern.search(answer):
            log_security_event(
                "guardrail.unsafe_markup_blocked",
                reason=reason,
                severity="error",
                stage="output_guardrail",
            )
            # Neutralize rather than discard the whole answer.
            answer = pattern.sub("[removed]", answer)

    return answer, None


def compute_confidence(
    hits: list[SearchHit], citations: list[Citation], invented: int, *, no_context: bool
) -> float:
    """Confidence from retrieval strength and citation coverage.

    Deliberately conservative: an uncited or partly-invented answer scores low
    even when retrieval looked good.
    """
    if no_context or not hits:
        return 0.0

    top_scores = [h.score for h in hits[:3]]
    retrieval = sum(top_scores) / len(top_scores)

    coverage = min(1.0, len(citations) / 2.0) if citations else 0.0
    penalty = 0.2 * invented

    confidence = (0.6 * retrieval) + (0.4 * coverage) - penalty
    return round(max(0.0, min(1.0, confidence)), 3)
