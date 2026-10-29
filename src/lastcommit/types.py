"""Shared data model for The Last Commit.

Everything the forensic engine and the interview see flows through these
dataclasses.  Typed evidence refs are the currency of the whole system: a
claim without one is treated as fabrication and stripped downstream.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------


@dataclass
class FileChange:
    path: str
    status: str  # 'A', 'M', 'D', 'R', 'T'
    insertions: int = 0
    deletions: int = 0
    old_path: str | None = None  # for renames


@dataclass
class CommitInfo:
    sha: str
    short_sha: str
    author_name: str
    author_email: str
    author_time: datetime
    committer_time: datetime
    summary: str
    message: str
    parents: list[str] = field(default_factory=list)
    is_merge: bool = False
    files: list[FileChange] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0

    def to_json(self) -> str:
        return json.dumps(
            {
                "sha": self.sha,
                "short": self.short_sha,
                "author": self.author_name,
                "date": self.author_time.isoformat(),
                "summary": self.summary,
                "files": [f.path for f in self.files],
                "insertions": self.insertions,
                "deletions": self.deletions,
            }
        )


@dataclass
class AuthorInfo:
    name: str
    email: str
    first_commit: datetime
    last_commit: datetime
    commit_count: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass
class BranchInfo:
    name: str
    tip_sha: str
    tip_summary: str
    tip_time: datetime
    commit_count: int
    merged: bool  # reachable from the default branch


# --------------------------------------------------------------------------
# Code (tree-sitter) & comments
# --------------------------------------------------------------------------


@dataclass
class SymbolInfo:
    name: str
    kind: str  # 'function' | 'class' | 'method'
    path: str
    language: str
    start_line: int
    end_line: int
    signature: str
    docstring: str | None = None
    is_stub: bool = False
    stub_reason: str | None = None


@dataclass
class TodoComment:
    path: str
    line: int
    kind: str  # TODO | FIXME | HACK | XXX
    text: str


@dataclass
class DocChunk:
    path: str
    heading: str  # section heading, '' for the preamble
    text: str
    start_line: int
    end_line: int
    last_modified: datetime  # last commit that touched the file


# --------------------------------------------------------------------------
# Evidence & findings
# --------------------------------------------------------------------------


@dataclass
class Evidence:
    """One concrete, checkable artifact backing a claim."""

    kind: str  # 'commit' | 'code' | 'todo' | 'doc' | 'branch' | 'author'
    ref: str  # sha | path:start-end | path:line | branch name | author name
    label: str  # human-readable one-liner
    quote: str | None = None  # supporting text found in the repo

    def render(self) -> str:
        return f"{self.ref} — {self.label}"


@dataclass
class Finding:
    category: str  # 'identity' | 'timeline' | 'unfinished' | 'regression' | 'decision' | 'roadmap'
    title: str
    description: str
    evidence: list[Evidence] = field(default_factory=list)
    severity: int = 2  # 1 = note, 2 = notable, 3 = alarming
    id: str = ""  # assigned after detection, e.g. 'F-007'
    data: dict = field(default_factory=dict)  # extra stats for the renderer/LLM

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "category": self.category,
                "title": self.title,
                "description": self.description,
                "severity": self.severity,
                "evidence": [
                    {"kind": e.kind, "ref": e.ref, "label": e.label, "quote": e.quote}
                    for e in self.evidence
                ],
                "data": self.data,
            }
        )


# --------------------------------------------------------------------------
# The full snapshot of a dead repository
# --------------------------------------------------------------------------


@dataclass
class RepoSnapshot:
    path: str  # filesystem path of the target repo
    name: str
    default_branch: str
    head_sha: str
    commits: list[CommitInfo] = field(default_factory=list)  # newest first
    authors: dict[str, AuthorInfo] = field(default_factory=dict)  # by name
    branches: list[BranchInfo] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    symbols: list[SymbolInfo] = field(default_factory=list)
    todos: list[TodoComment] = field(default_factory=list)
    docs: list[DocChunk] = field(default_factory=list)
    manifests: dict[str, str] = field(default_factory=dict)  # path -> content
    readme_text: str = ""
    changelog_text: str = ""
    file_stats: dict[str, dict] = field(default_factory=dict)
    # path -> {commits, insertions, deletions, first_touched, last_touched}
    languages: dict[str, int] = field(default_factory=dict)  # ext -> line count
    parse_errors: list[str] = field(default_factory=list)

    @property
    def first_commit_time(self) -> datetime | None:
        return min((c.author_time for c in self.commits), default=None)

    @property
    def last_commit_time(self) -> datetime | None:
        return max((c.author_time for c in self.commits), default=None)


def utc(ts: int | float) -> datetime:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)
