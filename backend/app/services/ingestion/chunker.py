"""Structural-boundary chunking with overlap.

Chunks split on paragraph boundaries first, then sentences, and only split a
sentence as a last resort. Every chunk keeps its page number and section so
citations stay precise.

Chunk parameters are configuration. Changing them requires an evaluation run -
see .claude/rules/testing.md section 5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings
from app.services.ingestion.extractors import ExtractedBlock

# ~4 characters per token, consistent with provider.estimate_tokens
_CHARS_PER_TOKEN = 4

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WHITESPACE_RE = re.compile(r"[ \t]+")


@dataclass(slots=True)
class Chunk:
    text: str
    index: int
    page_number: int | None = None
    section: str | None = None
    modality: str = "text"


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split_long(segment: str, max_chars: int) -> list[str]:
    """Sentence-split an oversized paragraph; hard-split only if still too big."""
    sentences = _SENTENCE_RE.split(segment)
    pieces: list[str] = []
    buffer = ""

    for sentence in sentences:
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            pieces.append(buffer)
        if len(sentence) <= max_chars:
            buffer = sentence
        else:
            for start in range(0, len(sentence), max_chars):
                pieces.append(sentence[start : start + max_chars])
            buffer = ""

    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_blocks(
    blocks: list[ExtractedBlock],
    *,
    chunk_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[Chunk]:
    """Turn extracted blocks into overlapping, citation-preserving chunks."""
    # `is None`, not `or`: an explicit 0 must mean zero, not "use the default".
    resolved_chunk = settings.chunk_size_tokens if chunk_tokens is None else chunk_tokens
    resolved_overlap = settings.chunk_overlap_tokens if overlap_tokens is None else overlap_tokens
    if resolved_overlap >= resolved_chunk:
        raise ValueError("chunk overlap must be smaller than chunk size")

    max_chars = resolved_chunk * _CHARS_PER_TOKEN
    overlap_chars = resolved_overlap * _CHARS_PER_TOKEN

    chunks: list[Chunk] = []
    index = 0

    for block in blocks:
        text = _normalize(block.text)
        if not text:
            continue

        # Build candidate segments at paragraph boundaries.
        segments: list[str] = []
        for paragraph in _PARAGRAPH_RE.split(text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if len(paragraph) <= max_chars:
                segments.append(paragraph)
            else:
                segments.extend(_split_long(paragraph, max_chars))

        # Pack segments up to the size limit.
        buffer = ""
        for segment in segments:
            candidate = f"{buffer}\n\n{segment}".strip() if buffer else segment
            if len(candidate) <= max_chars:
                buffer = candidate
                continue

            if buffer:
                chunks.append(
                    Chunk(
                        text=buffer,
                        index=index,
                        page_number=block.page_number,
                        section=block.section,
                        modality=block.modality,
                    )
                )
                index += 1
                tail = buffer[-overlap_chars:] if overlap_chars else ""
                buffer = f"{tail}\n\n{segment}".strip() if tail else segment
            else:
                buffer = segment

        if buffer.strip():
            chunks.append(
                Chunk(
                    text=buffer.strip(),
                    index=index,
                    page_number=block.page_number,
                    section=block.section,
                    modality=block.modality,
                )
            )
            index += 1

    return chunks
