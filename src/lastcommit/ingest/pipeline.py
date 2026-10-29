"""Ingest pipeline: one call turns a repository path into a full
`RepoSnapshot` (git history + AST symbols + todos + docs)."""

from __future__ import annotations

import time

from ..types import RepoSnapshot
from . import code_parser, docs as docs_mod
from .git_history import extract_git_history


def build_snapshot(repo_path: str, verbose: bool = False) -> RepoSnapshot:
    t0 = time.time()
    snap = extract_git_history(repo_path)
    code_parser.parse_repository(repo_path, snap)
    docs_mod.extract_docs(repo_path, snap)
    if verbose:
        print(f"ingested {snap.name}: {len(snap.commits)} commits, "
              f"{len(snap.symbols)} symbols, {len(snap.todos)} todos, "
              f"{len(snap.docs)} doc chunks in {time.time() - t0:.2f}s")
        if snap.parse_errors:
            print(f"parse warnings: {len(snap.parse_errors)}")
    return snap
