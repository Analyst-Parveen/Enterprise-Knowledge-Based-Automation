"""Reranking: reorder vector hits using lexical signal the embedding missed.

Bedrock has no cheap dedicated reranker in every region, and a rerank LLM call
per query would blow the $20 budget. This is a BM25-style lexical rerank fused
with the vector score - cheap, deterministic, and it measurably beats raw vector
order on keyword-heavy enterprise queries (policy numbers, product names, dates).

If a hosted reranker is added later, it slots in behind this same function.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from app.services.vector import SearchHit

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Weight of lexical score in the fused ranking. Vector similarity keeps the
# majority weight; lexical breaks ties and rescues exact-term matches.
_LEXICAL_WEIGHT = 0.35

_STOPWORDS = frozenset(
    """a an and are as at be by for from has have how in is it its of on or that the
    to was what when where which who why with your you our""".split()
)


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def _bm25_scores(
    query: str, hits: list[SearchHit], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """Standard BM25 over the retrieved set."""
    query_terms = _tokens(query)
    if not query_terms:
        return [0.0] * len(hits)

    docs = [_tokens(h.text) for h in hits]
    lengths = [len(d) for d in docs]
    avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0
    if avg_len == 0:
        return [0.0] * len(hits)

    n_docs = len(docs)
    doc_freq = Counter()
    for doc in docs:
        for term in set(doc):
            doc_freq[term] += 1

    scores: list[float] = []
    for doc, length in zip(docs, lengths, strict=True):
        counts = Counter(doc)
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            df = doc_freq.get(term, 0)
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * (length / avg_len))
            score += idf * (tf * (k1 + 1)) / denom
        scores.append(score)

    return scores


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def rerank(query: str, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]:
    """Fuse cosine similarity with normalized BM25, then truncate.

    Only BM25 is min-max normalized, because it is unbounded. Cosine scores are
    already on a comparable [0,1] scale and are used raw - min-max normalizing
    them would stretch a trivial 0.02 gap into a decisive one whenever the
    candidate set is small, which drowns out the lexical signal entirely.
    """
    if len(hits) <= 1:
        return hits[:top_k]

    lexical = _normalize(_bm25_scores(query, hits))
    vector_scores = [max(0.0, min(1.0, h.score)) for h in hits]

    fused = [
        ((1 - _LEXICAL_WEIGHT) * v + _LEXICAL_WEIGHT * lx, index)
        for index, (v, lx) in enumerate(zip(vector_scores, lexical, strict=True))
    ]
    fused.sort(key=lambda pair: pair[0], reverse=True)

    return [hits[index] for _, index in fused[:top_k]]
