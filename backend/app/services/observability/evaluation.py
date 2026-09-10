"""RAG evaluation harness.

Metrics (PROJECT.md section 11): retrieval precision, retrieval recall / hit
rate, answer relevance, faithfulness, citation correctness, factual correctness,
latency, token usage, estimated cost.

Run this after ANY change to embeddings, chunking, retrieval parameters,
reranking, prompts, or model routing. See .claude/rules/testing.md section 5.

The negative cases matter as much as the positive ones: a system that answers
everything confidently is worse than one that admits ignorance.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.context import RequestContext
from app.core.logging import get_logger
from app.services.rag.pipeline import ChatResponse

logger = get_logger(__name__)

DATASET_PATH = Path(__file__).resolve().parents[4] / "evaluation" / "datasets" / "golden_set.json"

_REFUSAL_MARKERS = ("could not find", "not find anything", "don't have", "no information")


@dataclass(slots=True)
class CaseResult:
    case_id: str
    department: str | None
    answerable: bool

    hit: bool = False  # correct document retrieved at all
    precision: float = 0.0  # fraction of retrieved chunks from expected docs
    answer_relevant: bool = False  # expected content present (or correctly refused)
    faithful: bool = True  # no claim without a citation
    citations_correct: bool = True  # every citation maps to an expected document
    refused_correctly: bool = True  # negative cases must refuse

    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EvaluationReport:
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def answerable(self) -> list[CaseResult]:
        return [c for c in self.cases if c.answerable]

    @property
    def negatives(self) -> list[CaseResult]:
        return [c for c in self.cases if not c.answerable]

    def _mean(self, values: list[float]) -> float:
        return round(statistics.fmean(values), 4) if values else 0.0

    def summary(self) -> dict[str, Any]:
        answerable = self.answerable
        negatives = self.negatives
        latencies = sorted(c.latency_ms for c in self.cases)

        def pct(index: float) -> int:
            if not latencies:
                return 0
            return latencies[min(len(latencies) - 1, int(len(latencies) * index))]

        return {
            "total_cases": len(self.cases),
            "answerable_cases": len(answerable),
            "negative_cases": len(negatives),
            # retrieval
            "retrieval_hit_rate": self._mean([1.0 if c.hit else 0.0 for c in answerable]),
            "retrieval_precision": self._mean([c.precision for c in answerable]),
            # answer quality
            "answer_relevance": self._mean([1.0 if c.answer_relevant else 0.0 for c in answerable]),
            "faithfulness": self._mean([1.0 if c.faithful else 0.0 for c in answerable]),
            "citation_correctness": self._mean(
                [1.0 if c.citations_correct else 0.0 for c in answerable]
            ),
            "factual_correctness": self._mean(
                [1.0 if (c.answer_relevant and c.citations_correct) else 0.0 for c in answerable]
            ),
            # the honesty metric - negatives must be refused
            "correct_refusal_rate": self._mean(
                [1.0 if c.refused_correctly else 0.0 for c in negatives]
            ),
            # operations
            "latency_p50_ms": pct(0.50),
            "latency_p95_ms": pct(0.95),
            "total_input_tokens": sum(c.input_tokens for c in self.cases),
            "total_output_tokens": sum(c.output_tokens for c in self.cases),
            "total_estimated_cost_usd": round(sum(c.estimated_cost for c in self.cases), 6),
            "mean_confidence": self._mean([c.confidence for c in self.cases]),
        }

    def by_department(self) -> dict[str, dict[str, float]]:
        """Per-department breakdown - a strong average can hide a weak department."""
        grouped: dict[str, list[CaseResult]] = {}
        for case in self.answerable:
            grouped.setdefault(case.department or "none", []).append(case)

        return {
            dept: {
                "cases": len(cases),
                "hit_rate": self._mean([1.0 if c.hit else 0.0 for c in cases]),
                "relevance": self._mean([1.0 if c.answer_relevant else 0.0 for c in cases]),
            }
            for dept, cases in grouped.items()
        }


def load_dataset(path: Path | None = None) -> dict[str, Any]:
    target = path or DATASET_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _looks_like_refusal(answer: str) -> bool:
    lowered = answer.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def score_case(case: dict[str, Any], response: ChatResponse) -> CaseResult:
    """Score one response against its expected outcome."""
    answerable = bool(case.get("answerable", True))
    result = CaseResult(
        case_id=case["id"],
        department=case.get("department"),
        answerable=answerable,
        latency_ms=response.latency_ms,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        estimated_cost=response.estimated_cost,
        confidence=response.confidence,
    )

    expected_docs = set(case.get("expected_document_ids", []))
    retrieved_docs = [c.get("document_id") for c in response.retrieved_chunks]

    # -- negative cases: the only correct behaviour is an honest refusal ----
    if not answerable:
        refused = _looks_like_refusal(response.answer) or not response.citations
        result.refused_correctly = refused
        if not refused:
            result.notes.append(
                "answered a question with no supporting document - possible hallucination"
            )
        return result

    # -- retrieval ---------------------------------------------------------
    if expected_docs:
        result.hit = any(doc in expected_docs for doc in retrieved_docs)
        if retrieved_docs:
            matches = sum(1 for doc in retrieved_docs if doc in expected_docs)
            result.precision = matches / len(retrieved_docs)
        if not result.hit:
            result.notes.append(f"expected document not retrieved: {sorted(expected_docs)}")

    # -- answer relevance --------------------------------------------------
    lowered = response.answer.lower()
    wanted = [w.lower() for w in case.get("expected_answer_contains", [])]
    result.answer_relevant = (not wanted) or any(w in lowered for w in wanted)
    if not result.answer_relevant:
        result.notes.append(f"answer missing expected content: {wanted}")

    for forbidden in case.get("must_not_contain", []):
        if forbidden.lower() in lowered:
            result.answer_relevant = False
            result.notes.append(f"answer contained forbidden text: {forbidden!r}")

    # -- faithfulness: a substantive answer must cite something ------------
    substantive = len(response.answer.split()) > 15 and not _looks_like_refusal(response.answer)
    if substantive and not response.citations:
        result.faithful = False
        result.notes.append("substantive answer with no citations")

    # -- citation correctness ---------------------------------------------
    if response.citations and expected_docs:
        cited_docs = {c.get("document_id") for c in response.citations}
        result.citations_correct = bool(cited_docs & expected_docs)
        if not result.citations_correct:
            result.notes.append(f"citations point outside expected documents: {cited_docs}")

    # Any citation referencing a chunk that was not retrieved is a hard fail.
    retrieved_chunk_ids = {c.get("chunk_id") for c in response.retrieved_chunks}
    for citation in response.citations:
        if citation.get("chunk_id") not in retrieved_chunk_ids:
            result.citations_correct = False
            result.faithful = False
            result.notes.append("citation references a chunk that was never retrieved")
            break

    return result


async def run_evaluation(
    session: Any, ctx: RequestContext, *, dataset: dict[str, Any] | None = None
) -> EvaluationReport:
    """Run every case through the real RAG pipeline."""
    from app.services.rag.pipeline import answer_question

    data = dataset or load_dataset()
    report = EvaluationReport()
    started = time.perf_counter()

    for case in data["cases"]:
        try:
            response = await answer_question(
                session, ctx, case["question"], department=case.get("department")
            )
        except Exception as exc:  # noqa: BLE001 - a failing case is a result, not a crash
            failed = CaseResult(
                case_id=case["id"],
                department=case.get("department"),
                answerable=bool(case.get("answerable", True)),
                answer_relevant=False,
                faithful=False,
                citations_correct=False,
                refused_correctly=False,
            )
            failed.notes.append(f"pipeline raised {type(exc).__name__}")
            report.cases.append(failed)
            continue

        report.cases.append(score_case(case, response))

    logger.info(
        "evaluation_completed",
        extra={
            "extra": {
                "cases": len(report.cases),
                "elapsed_s": round(time.perf_counter() - started, 2),
                **report.summary(),
            }
        },
    )
    return report


def render_markdown(report: EvaluationReport, *, git_sha: str = "unknown") -> str:
    """Render a report for docs/reports/. Failures are named, not averaged away."""
    summary = report.summary()
    lines = [
        "# RAG Evaluation Report",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Git SHA | `{git_sha}` |",
        f"| Cases | {summary['total_cases']} "
        f"({summary['answerable_cases']} answerable, {summary['negative_cases']} negative) |",
        "",
        "## Retrieval",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Hit rate | {summary['retrieval_hit_rate']:.1%} |",
        f"| Precision | {summary['retrieval_precision']:.1%} |",
        "",
        "## Answer quality",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Answer relevance | {summary['answer_relevance']:.1%} |",
        f"| Faithfulness | {summary['faithfulness']:.1%} |",
        f"| Citation correctness | {summary['citation_correctness']:.1%} |",
        f"| Factual correctness | {summary['factual_correctness']:.1%} |",
        f"| **Correct refusal rate** | **{summary['correct_refusal_rate']:.1%}** |",
        "",
        "## Operations",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Latency p50 | {summary['latency_p50_ms']} ms |",
        f"| Latency p95 | {summary['latency_p95_ms']} ms |",
        f"| Input tokens | {summary['total_input_tokens']:,} |",
        f"| Output tokens | {summary['total_output_tokens']:,} |",
        f"| Estimated cost | ${summary['total_estimated_cost_usd']:.6f} |",
        "",
        "## By department",
        "",
        "| Department | Cases | Hit rate | Relevance |",
        "|---|---|---|---|",
    ]
    for dept, stats in sorted(report.by_department().items()):
        lines.append(
            f"| {dept} | {int(stats['cases'])} | "
            f"{stats['hit_rate']:.1%} | {stats['relevance']:.1%} |"
        )

    failures = [c for c in report.cases if c.notes]
    lines += ["", "## Failures", ""]
    if not failures:
        lines.append("None. Every case met its expectation.")
    else:
        for case in failures:
            for note in case.notes:
                lines.append(f"- **{case.case_id}** — {note}")

    return "\n".join(lines) + "\n"


_WORD_RE = re.compile(r"\w+")
