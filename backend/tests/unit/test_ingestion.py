"""Unit tests for extraction, chunking, reranking, and model routing."""

from __future__ import annotations

import pytest

from app.services.ai.registry import (
    ModelRole,
    estimate_cost,
    resolve,
    validate_vision_routing,
)
from app.services.ingestion.chunker import chunk_blocks
from app.services.ingestion.extractors import (
    ExtractedBlock,
    extract_csv,
    extract_markdown,
    extract_plaintext,
)
from app.services.rag.rerank import rerank
from app.services.vector import SearchHit


class TestExtractors:
    def test_plaintext(self) -> None:
        blocks = extract_plaintext(b"Hello world.")
        assert blocks[0].text == "Hello world."

    def test_empty_input_yields_nothing(self) -> None:
        assert extract_plaintext(b"   ") == []

    def test_markdown_splits_on_headings(self) -> None:
        md = b"# Travel\nDomestic rules.\n\n# Expenses\nReceipts required."
        blocks = extract_markdown(md)
        sections = [b.section for b in blocks]
        assert "Travel" in sections and "Expenses" in sections

    def test_csv_keeps_headers_with_each_row(self) -> None:
        """A retrieved fragment must still be self-describing."""
        csv_bytes = b"region,spend\nEMEA,120000\nAPAC,90000\n"
        blocks = extract_csv(csv_bytes)
        text = blocks[0].text
        assert "region: EMEA" in text
        assert "spend: 120000" in text
        assert "region: APAC" in text

    def test_csv_handles_bom(self) -> None:
        blocks = extract_csv(b"\xef\xbb\xbfname,value\nA,1\n")
        assert blocks and "name: A" in blocks[0].text

    def test_pdf_rejects_garbage(self) -> None:
        from app.core.exceptions import ValidationError
        from app.services.ingestion.extractors import extract_pdf

        with pytest.raises(ValidationError):
            extract_pdf(b"this is definitely not a pdf")


class TestChunker:
    def test_short_text_is_one_chunk(self) -> None:
        chunks = chunk_blocks([ExtractedBlock(text="Short policy note.")])
        assert len(chunks) == 1

    def test_long_text_is_split(self) -> None:
        long_text = "\n\n".join(f"Paragraph {i} about travel policy rules." * 20 for i in range(20))
        chunks = chunk_blocks([ExtractedBlock(text=long_text)], chunk_tokens=100, overlap_tokens=10)
        assert len(chunks) > 1

    def test_page_number_preserved(self) -> None:
        """Citations depend on this."""
        chunks = chunk_blocks([ExtractedBlock(text="Content on page 7." * 5, page_number=7)])
        assert all(c.page_number == 7 for c in chunks)

    def test_section_preserved(self) -> None:
        chunks = chunk_blocks(
            [ExtractedBlock(text="Reimbursement details." * 5, section="Expenses")]
        )
        assert all(c.section == "Expenses" for c in chunks)

    def test_chunks_respect_size_budget(self) -> None:
        long_text = "word " * 5000
        chunks = chunk_blocks([ExtractedBlock(text=long_text)], chunk_tokens=100, overlap_tokens=0)
        max_chars = 100 * 4
        # Allow a small margin for boundary joining.
        assert all(len(c.text) <= max_chars * 1.2 for c in chunks)

    def test_indexes_are_sequential(self) -> None:
        chunks = chunk_blocks(
            [ExtractedBlock(text="para " * 400)], chunk_tokens=50, overlap_tokens=5
        )
        assert [c.index for c in chunks] == list(range(len(chunks)))

    def test_empty_blocks_produce_no_chunks(self) -> None:
        assert chunk_blocks([ExtractedBlock(text="  ")]) == []


class TestModelRegistry:
    def test_embedding_role_has_a_dimension(self) -> None:
        spec = resolve(ModelRole.EMBEDDING)
        assert spec.dimension and spec.dimension > 0

    def test_gpt_oss_is_text_only(self) -> None:
        """gpt-oss models cannot accept images - routing must refuse."""
        from app.services.ai.registry import ModelSpec

        spec = ModelSpec(
            model_id="openai.gpt-oss-20b-1:0",
            role=ModelRole.CHAT_PRIMARY,
            supports_vision=False,
            input_cost_per_1m=0.07,
            output_cost_per_1m=0.30,
        )
        with pytest.raises(ValueError, match="text-only"):
            validate_vision_routing(spec)

    def test_nova_lite_supports_vision(self) -> None:
        from app.services.ai.registry import _supports_vision

        assert _supports_vision("amazon.nova-lite-v1:0")

    def test_titan_embed_is_not_a_vision_model(self) -> None:
        from app.services.ai.registry import _supports_vision

        assert not _supports_vision("amazon.titan-embed-text-v2:0")

    def test_cost_estimation(self) -> None:
        spec = resolve(ModelRole.CHAT_PRIMARY)
        cost = estimate_cost(spec, 1_000_000, 1_000_000)
        assert cost == pytest.approx(spec.input_cost_per_1m + spec.output_cost_per_1m)

    def test_zero_tokens_cost_nothing(self) -> None:
        assert estimate_cost(resolve(ModelRole.CHAT_PRIMARY), 0, 0) == 0.0


class TestRerank:
    def _hit(self, text: str, score: float) -> SearchHit:
        return SearchHit(score=score, payload={"text": text, "chunk_id": text[:8]})

    def test_exact_term_match_promoted(self) -> None:
        """Lexical signal should rescue a keyword match the vector ranked lower."""
        hits = [
            self._hit("General guidance about company travel arrangements.", 0.72),
            self._hit("The domestic hotel reimbursement limit is 150 USD.", 0.70),
        ]
        ranked = rerank("domestic hotel reimbursement limit", hits, top_k=2)
        assert "150 USD" in ranked[0].text

    def test_truncates_to_top_k(self) -> None:
        hits = [self._hit(f"chunk {i} travel policy", 0.5 + i / 100) for i in range(10)]
        assert len(rerank("travel policy", hits, top_k=3)) == 3

    def test_single_hit_passthrough(self) -> None:
        hits = [self._hit("only one", 0.9)]
        assert rerank("anything", hits, top_k=5) == hits

    def test_empty_input(self) -> None:
        assert rerank("q", [], top_k=5) == []


class TestLocalProvider:
    """The $0 dev provider must behave deterministically."""

    @pytest.mark.asyncio
    async def test_embeddings_are_deterministic(self) -> None:
        from app.services.ai.provider import LocalProvider

        provider = LocalProvider()
        first = (await provider.embed(["travel policy"])).vectors[0]
        second = (await provider.embed(["travel policy"])).vectors[0]
        assert first == second

    @pytest.mark.asyncio
    async def test_similar_text_is_closer_than_unrelated(self) -> None:
        from app.services.ai.provider import LocalProvider
        from app.services.rag.cache import _cosine

        provider = LocalProvider()
        result = await provider.embed(
            [
                "travel policy reimbursement",
                "travel policy reimbursement rules",
                "quantum chromodynamics lattice",
            ]
        )
        a, b, c = result.vectors
        assert _cosine(a, b) > _cosine(a, c)

    @pytest.mark.asyncio
    async def test_embeddings_are_normalized(self) -> None:
        import math

        from app.services.ai.provider import LocalProvider

        vector = (await LocalProvider().embed(["hello world"])).vectors[0]
        norm = math.sqrt(sum(v * v for v in vector))
        assert norm == pytest.approx(1.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_local_provider_costs_nothing(self) -> None:
        from app.services.ai.provider import LocalProvider

        result = await LocalProvider().embed(["anything"])
        assert result.estimated_cost == 0.0
