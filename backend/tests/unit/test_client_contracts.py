"""Contract tests against installed third-party clients.

These exist because qdrant-client 1.19 removed QdrantClient.search(), which the
retrieval layer called. Nothing caught it until a live query returned 502: the
unit tests mocked the client, and the integration tests stubbed the search
function itself.

A test that asserts the methods we actually call still exist turns a silent
runtime break into a failing build on the next dependency bump.
"""

from __future__ import annotations

import inspect

import pytest
from qdrant_client import QdrantClient


class TestQdrantClientSurface:
    """Every QdrantClient method app.services.vector depends on must exist."""

    @pytest.mark.parametrize(
        "method",
        [
            "query_points",  # search - NOT `search`, removed in 1.19
            "upsert",
            "delete",
            "count",
            "get_collections",
            "get_collection",
            "create_collection",
            "create_payload_index",
        ],
    )
    def test_method_exists(self, method: str) -> None:
        assert hasattr(QdrantClient, method), (
            f"qdrant-client no longer provides {method!r}. "
            "app/services/vector.py calls it - update the call site."
        )

    def test_query_points_accepts_the_arguments_we_pass(self) -> None:
        params = inspect.signature(QdrantClient.query_points).parameters
        for arg in (
            "collection_name",
            "query",
            "query_filter",
            "limit",
            "score_threshold",
            "with_payload",
        ):
            assert arg in params, f"query_points no longer accepts {arg!r}"

    def test_we_do_not_call_the_removed_search_method(self) -> None:
        """Guard against reintroducing the removed API.

        Comments are stripped first - the call site carries an explanatory note
        naming the removed method, and matching that would be a false positive.
        """
        import re

        import app.services.vector as vector_module

        source = inspect.getsource(vector_module)
        code_only = "\n".join(re.sub(r"#.*$", "", line) for line in source.splitlines())

        assert not re.search(r"get_client\(\)\s*\.\s*search\s*\(", code_only), (
            "app/services/vector.py calls client.search(), which qdrant-client "
            "removed in 1.19. Use query_points() instead."
        )


class TestSeedContent:
    """A seeded document with no body is invisible to retrieval."""

    def test_every_ready_document_has_content_to_index(self) -> None:
        from seeds.documents import SEED_DOCUMENT_BODIES
        from seeds.seed import SEED_DOCUMENTS

        # The last entry is the deliberately-FAILED ingestion job, which is
        # meant to have no content so the failure path is visible in the UI.
        ready = SEED_DOCUMENTS[:-1]

        for doc_id, name, *_ in ready:
            bodies = SEED_DOCUMENT_BODIES.get(doc_id)
            assert bodies, (
                f"{name} ({doc_id}) has no body in seeds/documents.py, so it "
                "would be listed in the UI but never retrievable."
            )

    def test_golden_set_facts_are_present_in_the_seed_corpus(self) -> None:
        """The evaluation dataset must be answerable from the seeded documents."""
        import json
        from pathlib import Path

        from seeds.documents import SEED_DOCUMENT_BODIES

        dataset = (
            Path(__file__).resolve().parents[3] / "evaluation" / "datasets" / "golden_set.json"
        )
        cases = json.loads(dataset.read_text(encoding="utf-8"))["cases"]

        for case in cases:
            if not case["answerable"]:
                continue
            for doc_id in case["expected_document_ids"]:
                corpus = " ".join(b for _, _, b in SEED_DOCUMENT_BODIES.get(doc_id, []))
                assert corpus, f"{case['id']}: expected document {doc_id} has no content"

                # At least one expected answer fragment must actually appear.
                wanted = case["expected_answer_contains"]
                if wanted:
                    assert any(w.lower() in corpus.lower() for w in wanted), (
                        f"{case['id']}: none of {wanted} appear in {doc_id}. "
                        "The golden set and the seed corpus have drifted apart."
                    )
