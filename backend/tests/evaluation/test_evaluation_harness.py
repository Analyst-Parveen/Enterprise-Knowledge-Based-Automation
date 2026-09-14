"""Tests for the evaluation harness itself.

If the scorer is wrong, every future evaluation number is wrong - so the scorer
gets tested like any other component, especially its ability to catch a
hallucination rather than reward it.
"""

from __future__ import annotations

from app.services.observability.evaluation import (
    EvaluationReport,
    load_dataset,
    render_markdown,
    score_case,
)
from app.services.rag.pipeline import ChatResponse


def make_response(
    *,
    answer: str = "The domestic hotel limit is 150 USD per night [S1].",
    doc_id: str = "seed-doc-travel-2026",
    chunk_id: str = "c1",
    with_citation: bool = True,
) -> ChatResponse:
    return ChatResponse(
        answer=answer,
        citations=(
            [{"source_number": 1, "document_id": doc_id, "chunk_id": chunk_id}]
            if with_citation
            else []
        ),
        retrieved_chunks=[{"chunk_id": chunk_id, "document_id": doc_id, "score": 0.8}],
        model_used="test",
        input_tokens=100,
        output_tokens=20,
        estimated_cost=0.0001,
        latency_ms=500,
        cache_hit=False,
        tenant_id="tenant-a",
        confidence=0.8,
    )


POSITIVE_CASE = {
    "id": "fin-001",
    "department": "finance",
    "question": "What is the domestic hotel reimbursement limit?",
    "expected_document_ids": ["seed-doc-travel-2026"],
    "expected_answer_contains": ["150"],
    "must_not_contain": [],
    "answerable": True,
}

NEGATIVE_CASE = {
    "id": "neg-001",
    "department": None,
    "question": "What is the policy on travel to Mars?",
    "expected_document_ids": [],
    "expected_answer_contains": [],
    "must_not_contain": [],
    "answerable": False,
}


class TestDataset:
    def test_dataset_loads(self) -> None:
        data = load_dataset()
        assert data["cases"]
        assert any(not c["answerable"] for c in data["cases"]), (
            "the dataset must include refusal cases, or hallucination goes unmeasured"
        )

    def test_every_case_is_well_formed(self) -> None:
        for case in load_dataset()["cases"]:
            assert case["id"] and case["question"]
            assert isinstance(case["answerable"], bool)
            if case["answerable"]:
                assert case["expected_document_ids"], f"{case['id']} has no expected document"


class TestScoringPositiveCases:
    def test_correct_answer_scores_well(self) -> None:
        result = score_case(POSITIVE_CASE, make_response())
        assert result.hit
        assert result.precision == 1.0
        assert result.answer_relevant
        assert result.faithful
        assert result.citations_correct

    def test_wrong_document_fails_the_hit(self) -> None:
        response = make_response(doc_id="seed-doc-unrelated")
        result = score_case(POSITIVE_CASE, response)
        assert not result.hit
        assert result.precision == 0.0
        assert result.notes

    def test_missing_expected_content_is_caught(self) -> None:
        response = make_response(answer="The policy covers travel arrangements [S1].")
        result = score_case(POSITIVE_CASE, response)
        assert not result.answer_relevant

    def test_substantive_answer_without_citation_is_unfaithful(self) -> None:
        response = make_response(
            answer=(
                "The domestic hotel limit is 150 USD per night and receipts are "
                "required for anything over 25 USD, per company policy."
            ),
            with_citation=False,
        )
        result = score_case(POSITIVE_CASE, response)
        assert not result.faithful

    def test_citation_to_unretrieved_chunk_is_a_hard_fail(self) -> None:
        """The single most important scorer behaviour."""
        response = make_response()
        response.citations = [
            {
                "source_number": 1,
                "document_id": "seed-doc-travel-2026",
                "chunk_id": "never-retrieved",
            }
        ]
        result = score_case(POSITIVE_CASE, response)
        assert not result.citations_correct
        assert not result.faithful


class TestScoringNegativeCases:
    def test_correct_refusal_passes(self) -> None:
        response = make_response(
            answer="I could not find anything in your knowledge base about that.",
            with_citation=False,
        )
        result = score_case(NEGATIVE_CASE, response)
        assert result.refused_correctly

    def test_confident_hallucination_fails(self) -> None:
        """A confident answer to an unanswerable question must be penalised."""
        response = make_response(
            answer="The Mars travel policy allows up to 500 USD per sol [S1].",
        )
        result = score_case(NEGATIVE_CASE, response)
        assert not result.refused_correctly
        assert any("hallucination" in n for n in result.notes)


class TestReport:
    def test_summary_has_every_required_metric(self) -> None:
        report = EvaluationReport(
            cases=[
                score_case(POSITIVE_CASE, make_response()),
                score_case(
                    NEGATIVE_CASE,
                    make_response(answer="I could not find that.", with_citation=False),
                ),
            ]
        )
        summary = report.summary()
        for metric in (
            "retrieval_precision",
            "retrieval_hit_rate",
            "answer_relevance",
            "faithfulness",
            "citation_correctness",
            "factual_correctness",
            "correct_refusal_rate",
            "latency_p50_ms",
            "latency_p95_ms",
            "total_input_tokens",
            "total_estimated_cost_usd",
        ):
            assert metric in summary, f"missing metric: {metric}"

    def test_per_department_breakdown(self) -> None:
        report = EvaluationReport(cases=[score_case(POSITIVE_CASE, make_response())])
        assert "finance" in report.by_department()

    def test_markdown_names_failures(self) -> None:
        bad = score_case(POSITIVE_CASE, make_response(doc_id="wrong-doc"))
        markdown = render_markdown(EvaluationReport(cases=[bad]), git_sha="abc123")
        assert "## Failures" in markdown
        assert "fin-001" in markdown

    def test_markdown_reports_clean_run(self) -> None:
        good = score_case(POSITIVE_CASE, make_response())
        markdown = render_markdown(EvaluationReport(cases=[good]))
        assert "None. Every case met its expectation." in markdown

    def test_empty_report_does_not_crash(self) -> None:
        summary = EvaluationReport().summary()
        assert summary["total_cases"] == 0
        assert summary["latency_p50_ms"] == 0
