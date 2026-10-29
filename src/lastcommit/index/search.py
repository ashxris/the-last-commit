"""Hybrid retrieval: BM25 (SQLite FTS5) + vector cosine, fused with
reciprocal rank fusion.  Degrades to keyword-only when embeddings are
unavailable."""

from __future__ import annotations

from .embeddings import BaseEmbedder
from .store import Chunk, ChunkStore, SearchHit

RRF_K = 60


def hybrid_search(
    store: ChunkStore,
    embedder: BaseEmbedder | None,
    query: str,
    kinds: list[str] | None = None,
    k: int = 8,
) -> list[SearchHit]:
    fts = store.fts_search(query, kinds=kinds, k=max(k * 4, 25))
    scores: dict[int, SearchHit] = {}

    for rank, (rid, _rank) in enumerate(fts):
        chunk = store.get_chunk(rid)
        if chunk is None:
            continue
        scores[rid] = SearchHit(chunk=chunk, score=0.0, sources=["fts"])

    if embedder is not None and query.strip():
        try:
            ids, matrix = store.all_embeddings()
            if len(ids):
                qvec = embedder.embed([query])[0]
                import numpy as np

                q = np.array(qvec, dtype=np.float32)
                norm = np.linalg.norm(q)
                if norm > 0:
                    q /= norm
                    sims = matrix @ q
                    order = sorted(range(len(ids)), key=lambda i: -sims[i])[: max(k * 4, 25)]
                    for rank, i in enumerate(order):
                        rid = ids[i]
                        if rid in scores:
                            scores[rid].sources.append("vec")
                        else:
                            chunk = store.get_chunk(rid)
                            if chunk is None:
                                continue
                            if kinds and chunk.kind not in kinds:
                                continue
                            scores[rid] = SearchHit(chunk=chunk, score=0.0, sources=["vec"])
                    vec_ranks = {ids[i]: r for r, i in enumerate(order)}
        except Exception:
            vec_ranks = {}
    else:
        vec_ranks = {}

    fts_ranks = {rid: r for r, (rid, _rank) in enumerate(fts)}
    for rid, hit in scores.items():
        s = 0.0
        if rid in fts_ranks:
            s += 1.0 / (RRF_K + fts_ranks[rid])
        if rid in vec_ranks:
            s += 1.0 / (RRF_K + vec_ranks[rid])
        hit.score = s

    hits = sorted(scores.values(), key=lambda h: -h.score)[:k]
    return hits


def pack_hits(hits: list[SearchHit], max_chars: int = 6000) -> str:
    """Render hits into a bounded evidence block for a prompt."""
    parts: list[str] = []
    used = 0
    for h in hits:
        text = h.chunk.render()
        if used + len(text) > max_chars:
            break
        parts.append(text)
        used += len(text)
    return "\n\n---\n\n".join(parts)
