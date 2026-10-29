"""Documentation chunking: README, CHANGELOG, docs/ — split by markdown
headings into citation-addressable sections."""

from __future__ import annotations

import re
from pathlib import Path

from ..types import DocChunk

MAX_CHUNK = 1200  # chars


def _split_markdown(text: str) -> list[tuple[str, int, str]]:
    """[(heading, start_line, body)] — the preamble before the first heading
    gets heading ''."""
    sections: list[tuple[str, int, str]] = []
    heading_re = re.compile(r"^(#{1,6})\s+(.*)$")
    lines = text.splitlines()
    cur_heading = ""
    cur_start = 1
    cur_body: list[str] = []
    for i, line in enumerate(lines, start=1):
        m = heading_re.match(line)
        if m:
            if cur_body:
                sections.append((cur_heading, cur_start, "\n".join(cur_body).strip()))
            cur_heading = m.group(2).strip()
            cur_start = i
            cur_body = [line]
        else:
            cur_body.append(line)
    if cur_body:
        sections.append((cur_heading, cur_start, "\n".join(cur_body).strip()))
    return [(h, s, b) for h, s, b in sections if b]


def _split_plain(text: str) -> list[tuple[str, int, str]]:
    paragraphs: list[tuple[str, int, str]] = []
    lines = text.splitlines()
    buf: list[str] = []
    start = 1
    for i, line in enumerate(lines, start=1):
        if line.strip() == "" and buf:
            paragraphs.append(("", start, "\n".join(buf).strip()))
            buf, start = [], i + 1
        else:
            buf.append(line)
    if buf:
        paragraphs.append(("", start, "\n".join(buf).strip()))
    return [p for p in paragraphs if p[2]]


def _split_long(heading: str, start_line: int, body: str) -> list[tuple[str, int, str]]:
    if len(body) <= MAX_CHUNK:
        return [(heading, start_line, body)]
    out = []
    chunk: list[str] = []
    size = 0
    for para in body.split("\n\n"):
        if size + len(para) > MAX_CHUNK and chunk:
            out.append((heading, start_line, "\n\n".join(chunk).strip()))
            chunk, size = [], 0
        chunk.append(para)
        size += len(para) + 2
    if chunk:
        out.append((heading, start_line, "\n\n".join(chunk).strip()))
    return out


def extract_docs(repo_path: str, snapshot) -> None:
    """Populate snapshot.docs with per-section chunks of doc files."""
    root = Path(repo_path)
    doc_files: list[Path] = []
    for p in root.rglob("*.md"):
        if ".git" in p.parts or ".lastcommit" in p.parts or "node_modules" in p.parts:
            continue
        doc_files.append(p)
    for p in list(root.glob("*.rst")) + list(root.glob("docs/*.txt")):
        doc_files.append(p)

    # last-modified per file from the snapshot's churn stats
    stats = snapshot.file_stats
    for f in sorted(set(doc_files)):
        rel = f.relative_to(root).as_posix()
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        st = stats.get(rel) or stats.get(rel.lower()) or {}
        last_mod = st.get("last_touched") or snapshot.last_commit_time
        if last_mod is None:
            continue
        splitter = _split_markdown if f.suffix == ".md" else _split_plain
        for heading, start, body in splitter(text):
            for h2, s2, b2 in _split_long(heading, start, body):
                snapshot.docs.append(
                    DocChunk(path=rel, heading=h2, text=b2, start_line=s2, end_line=s2 + b2.count("\n"),
                             last_modified=last_mod)
                )
