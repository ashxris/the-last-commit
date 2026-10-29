"""Build the retrieval corpus from a RepoSnapshot: commit chunks, code
symbol chunks, doc chunks, todo chunks — then (best effort) embed them."""

from __future__ import annotations

from ..ingest.pipeline import build_snapshot
from ..types import RepoSnapshot
from .embeddings import BaseEmbedder, get_embedder
from .store import Chunk, ChunkStore

MAX_COMMIT_CHUNKS = 600


def snapshot_to_chunks(snap: RepoSnapshot) -> list[Chunk]:
    chunks: list[Chunk] = []

    for c in snap.commits[:MAX_COMMIT_CHUNKS]:
        files = ", ".join(dict.fromkeys(f.path for f in c.files))[:300]
        chunks.append(
            Chunk(
                kind="commit",
                ref_id=c.short_sha,
                title=f"{c.short_sha} {c.summary}",
                content=(
                    f"Commit {c.sha[:12]} by {c.author_name} on {c.author_time:%Y-%m-%d}\n"
                    f"Message: {c.message}\n"
                    f"Files changed ({len(c.files)}): {files}\n"
                    f"Lines: +{c.insertions}/-{c.deletions}"
                ),
                meta={"sha": c.sha, "author": c.author_name, "date": c.author_time.isoformat()},
            )
        )

    for s in snap.symbols:
        doc = f"\nDocstring: {s.docstring}" if s.docstring else ""
        stub = f" [STUB: {s.stub_reason}]" if s.is_stub else ""
        chunks.append(
            Chunk(
                kind="code",
                ref_id=f"{s.path}:{s.start_line}-{s.end_line}",
                title=f"{s.path} :: {s.name} ({s.kind}){stub}",
                content=f"File {s.path}, lines {s.start_line}-{s.end_line} ({s.language}){stub}\n"
                        f"{s.signature}{doc}",
                meta={"path": s.path, "name": s.name, "kind": s.kind,
                      "is_stub": s.is_stub, "line": s.start_line},
            )
        )

    for d in snap.docs:
        title = f"{d.path} :: {d.heading}" if d.heading else f"{d.path} (preamble)"
        chunks.append(
            Chunk(
                kind="doc",
                ref_id=f"{d.path}:{d.start_line}-{d.end_line}",
                title=title,
                content=d.text,
                meta={"path": d.path, "last_modified": d.last_modified.isoformat()},
            )
        )

    for t in snap.todos:
        chunks.append(
            Chunk(
                kind="todo",
                ref_id=f"{t.path}:{t.line}",
                title=f"{t.kind} {t.path}:{t.line}",
                content=f"{t.kind} in {t.path} line {t.line}: {t.text}",
                meta={"path": t.path, "line": t.line, "marker": t.kind},
            )
        )
    return chunks


def ingest_repository(repo_path: str, embed_config, verbose: bool = False) -> tuple[RepoSnapshot, ChunkStore, BaseEmbedder | None]:
    """Full ingest: snapshot → store (reset each time). Returns snapshot,
    open store, and the embedder (or None if embeddings are unavailable)."""
    snap = build_snapshot(repo_path, verbose=verbose)
    store = ChunkStore(repo_path)
    store.reset()
    store.set_meta("repo", snap.name)
    store.set_meta("head_sha", snap.head_sha)
    store.set_meta("ingested_at", snap.last_commit_time.isoformat() if snap.last_commit_time else "")

    chunks = snapshot_to_chunks(snap)
    for chunk in chunks:
        store.add_chunk(chunk)
    store.commit()

    embedder = None
    if embed_config.provider != "none":
        embedder = get_embedder(embed_config)
        if embedder is None:
            if verbose:
                print("embeddings unavailable — continuing keyword-only")
        else:
            store.set_meta("embed_model", embedder.name)
            batch: list[tuple[int, str]] = []
            for cid, chunk in store.all_chunks():
                batch.append((cid, f"{chunk.title}\n{chunk.content[:2000]}"))
            for start in range(0, len(batch), 64):
                part = batch[start : start + 64]
                vectors = embedder.embed([t for _, t in part])
                for (cid, _), vec in zip(part, vectors):
                    store.save_embedding(cid, vec)
            store.commit()
            if verbose:
                print(f"embedded {len(batch)} chunks with {embedder.name} (dim {embedder.dim})")
    return snap, store, embedder
