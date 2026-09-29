"""Greetings answer as greetings; everything else stays grounded.

The risk in adding a conversational path to a RAG system is that it becomes a
way to get ungrounded answers. It cannot here: the match is exact on a short
pleasantry and the reply is fixed text, so no model is called and nothing can be
invented. These tests pin that boundary - particularly that a greeting with a
real question attached still goes through retrieval.
"""

from __future__ import annotations

import pytest

from app.services.rag import prompts


@pytest.mark.parametrize(
    "greeting",
    [
        "hi",
        "Hi",
        "HI",
        "hey",
        "hye",
        "hello",
        "hlw",
        "how are you",
        "How are you?",
        "thanks",
        "bye",
    ],
)
def test_a_bare_greeting_gets_a_reply(greeting: str) -> None:
    assert prompts.small_talk_reply(greeting) is not None


def test_case_punctuation_and_spacing_do_not_matter() -> None:
    for form in ("hello", "Hello!", "  HELLO  ", "hello?", "hello."):
        assert prompts.small_talk_reply(form) is not None


@pytest.mark.parametrize(
    "question",
    [
        "hi, what is the leave policy",
        "hello can you show me the salary bands",
        "how are the travel limits calculated",
        "who are the directors listed in the filing",
        "thanks for the report, what was the revenue",
        "what is the reimbursement limit",
        "summarise the HR handbook",
    ],
)
def test_a_real_question_is_never_treated_as_small_talk(question: str) -> None:
    """The failure that would matter: a document question answered from nothing."""
    assert prompts.small_talk_reply(question) is None


def test_no_reply_mentions_a_document_or_makes_a_claim() -> None:
    """Small talk must not assert anything about the knowledge base contents."""
    for reply in prompts.SMALL_TALK.values():
        lowered = reply.lower()
        assert "according to" not in lowered
        assert "the document says" not in lowered
        assert "[s" not in lowered, "a canned reply must never carry a citation marker"


def test_every_key_is_already_normalised() -> None:
    """A key with capitals or padding could never be matched."""
    for key in prompts.SMALL_TALK:
        assert key == key.strip().lower()
        assert " ".join(key.split()) == key
