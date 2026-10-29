"""SQLite-backed chunk store: the corpus the forensic engine and the
interviewer retrieve from.  One file at <repo>/.lastcommit/index.db."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,          -- 'commit' | 'code' | 'doc' | 'todo'
    ref_id TEXT NOT NULL,        -- sha | path:start-end | path:line | path#heading
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}',
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_kind ON chunks(kind);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(title, content, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Chunk:
    kind: str
    ref_id: str
    title: str
    content: str
    meta: dict = field(default_factory=dict)

    def render(self) -> str:
        """Rendered form shown to the LLM as evidence."""
        head = f"[{self.kind}] {self.title} ({self.ref_id})"
        return f"{head}\n{self.content}"


@dataclass
class SearchHit:
    chunk: Chunk
    score: float
    sources: list[str] = field(default_factory=list)


class ChunkStore:
    def __init__(self, repo_path: str):
        self.dir = Path(repo_path) / ".lastcommit"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dir / "index.db")
        self.db.executescript(SCHEMA)

    # -- meta ---------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # -- writing ------------------------------------------------------------
    def reset(self) -> None:
        self.db.executescript("DELETE FROM chunks; DELETE FROM chunks_fts; DELETE FROM meta;")

    def add_chunk(self, chunk: Chunk) -> int:
        cur = self.db.execute(
            "INSERT INTO chunks(kind, ref_id, title, content, meta) VALUES (?, ?, ?, ?, ?)",
            (chunk.kind, chunk.ref_id, chunk.title, chunk.content, json.dumps(chunk.meta)),
        )
        rowid = cur.lastrowid
        self.db.execute(
            "INSERT INTO chunks_fts(rowid, title, content) VALUES (?, ?, ?)",
            (rowid, chunk.title, chunk.content),
        )
        return rowid

    def save_embedding(self, chunk_id: int, vector) -> None:
        import struct

        blob = struct.pack(f"{len(vector)}f", *vector)
        self.db.execute("UPDATE chunks SET embedding = ? WHERE id = ?", (blob, chunk_id))

    def commit(self) -> None:
        self.db.commit()

    # -- reading --------------------------------------------------------------
    def fts_search(self, query: str, kinds: list[str] | None = None, k: int = 25) -> list[tuple[int, float]]:
        """BM25-ranked ids (lower rank = better)."""
        terms = [t for t in re.findall(r"\w+", query) if len(t) > 1][:8]
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        try:
            rows = self.db.execute(
                "SELECT rowid, bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, k * 4),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        if kinds:
            keep = []
            for rid, rank in rows:
                kind = self.db.execute("SELECT kind FROM chunks WHERE id = ?", (rid,)).fetchone()
                if kind and kind[0] in kinds:
                    keep.append((rid, rank))
            return keep[:k]
        return rows[:k]

    def all_chunks(self, kinds: list[str] | None = None) -> list[tuple[int, Chunk]]:
        sql = "SELECT id, kind, ref_id, title, content, meta FROM chunks"
        params: tuple = ()
        if kinds:
            sql += " WHERE kind IN (%s)" % ",".join("?" * len(kinds))
            params = tuple(kinds)
        rows = self.db.execute(sql, params).fetchall()
        return [
            (r[0], Chunk(kind=r[1], ref_id=r[2], title=r[3], content=r[4], meta=json.loads(r[5])))
            for r in rows
        ]

    def all_embeddings(self) -> tuple[list[int], "object"]:
        """(ids, numpy matrix of normalized vectors) for chunks that have one."""
        import numpy as np

        rows = self.db.execute(
            "SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL"
        ).fetchall()
        if not rows:
            return [], np.zeros((0, 0))
        import struct

        dim = len(struct.unpack(f"{len(rows[0][1]) // 4}f", rows[0][1]))
        ids, vecs = [], []
        for rid, blob in rows:
            vec = np.array(struct.unpack(f"{len(blob) // 4}f", blob), dtype=np.float32)
            norm = np.linalg.norm(vec)
            ids.append(rid)
            vecs.append(vec / norm if norm > 0 else vec)
        return ids, np.vstack(vecs)

    def get_chunk(self, chunk_id: int) -> Chunk | None:
        row = self.db.execute(
            "SELECT kind, ref_id, title, content, meta FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if not row:
            return None
        return Chunk(kind=row[0], ref_id=row[1], title=row[2], content=row[3], meta=json.loads(row[4]))

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def close(self) -> None:
        self.db.close()
