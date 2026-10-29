"""Interview tool belt: the actions the maintainer-persona can take to
ground its answers — real git reads and real retrieval over the index."""

from __future__ import annotations

import json

from ..index.embeddings import BaseEmbedder
from ..index.search import hybrid_search, pack_hits
from ..index.store import ChunkStore
from ..ingest import git_history

MAX_TOOL_OUTPUT = 2800
SLIM_TOOL_OUTPUT = 1200  # web mode: every token of tool output is re-sent each round


class ToolBelt:
    def __init__(self, repo_path: str, store: ChunkStore, embedder: BaseEmbedder | None,
                 slim: bool = False):
        self.repo_path = repo_path
        self.store = store
        self.embedder = embedder
        self.max_output = SLIM_TOOL_OUTPUT if slim else MAX_TOOL_OUTPUT

    # -- schemas -------------------------------------------------------------
    def schemas(self) -> list[dict]:
        def fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required or [],
                    },
                },
            }

        if self.max_output == SLIM_TOOL_OUTPUT:
            # web interview: five terse tools — schemas ride along on every
            # request, so they must stay small
            return [
                fn("search_repo", "Search commits, code, docs, TODOs.",
                   {"query": {"type": "string"}}, ["query"]),
                fn("show_commit", "Show a commit's message, author, date, diff.",
                   {"sha": {"type": "string"}}, ["sha"]),
                fn("file_at_rev", "Read a file as of a revision.",
                   {"path": {"type": "string"}, "rev": {"type": "string"}}, ["path"]),
                fn("timeline", "Commit cadence by month plus the author roster.", {}),
                fn("list_branches", "All branches with tips and merge status.", {}),
            ]

        return [
            fn("show_commit", "Show a commit's full message, author, date, and diff. "
               "Use for 'why did you do X' or 'what happened at <sha>'.",
               {"sha": {"type": "string", "description": "commit sha (short or full)"}}, ["sha"]),
            fn("file_at_rev", "Read a file's full contents as of a revision.",
               {"path": {"type": "string"}, "rev": {"type": "string", "description": "sha/branch/HEAD~N, default HEAD"}},
               ["path"]),
            fn("blame_file", "Who last touched each region of a file, with the commits responsible.",
               {"path": {"type": "string"}}, ["path"]),
            fn("search_repo", "Hybrid keyword+semantic search over commits, code symbols, docs, and TODOs.",
               {"query": {"type": "string"}}, ["query"]),
            fn("search_history", "Search commit messages and metadata only.",
               {"query": {"type": "string"}}, ["query"]),
            fn("timeline", "Monthly commit cadence, first/last commits, and the author roster. "
               "Call this first for questions about the project's history.",
               {}),
            fn("list_branches", "All branches with tips, dates, and merged/dangling status.", {}),
            fn("list_todos", "List TODO/FIXME/HACK/SKIP markers, optionally under one path prefix.",
               {"path_prefix": {"type": "string"}}),
        ]

    # -- dispatch --------------------------------------------------------------
    def execute(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return "ERROR: malformed JSON arguments"
        try:
            result = self._dispatch(name, args)
        except Exception as exc:
            return f"ERROR: {exc}"
        return result[: self.max_output]

    def _dispatch(self, name: str, args: dict) -> str:
        if name == "show_commit":
            c = git_history.show_commit(self.repo_path, args["sha"])
            return (f"Commit {c['sha'][:12]} — {c['author']}, {c['date']}\n"
                    f"Message:\n{c['message']}\n\nDiff:\n```diff\n{c['patch']}\n```")

        if name == "file_at_rev":
            rev = args.get("rev") or "HEAD"
            text = git_history.read_blob_at(self.repo_path, rev, args["path"])
            return f"Content of {args['path']} at {rev}:\n```\n{text[:MAX_TOOL_OUTPUT]}\n```"

        if name == "blame_file":
            hunks = git_history.blame_file(self.repo_path, args["path"])
            lines = [f"L{start:4d}+ {sha} {author}" for start, sha, author in hunks]
            return f"Blame for {args['path']} (line-range start, commit, author):\n" + "\n".join(lines)

        if name == "search_repo":
            hits = hybrid_search(self.store, self.embedder, args["query"], k=8)
            return pack_hits(hits, max_chars=MAX_TOOL_OUTPUT) or "No matches."

        if name == "search_history":
            hits = hybrid_search(self.store, None, args["query"], kinds=["commit"], k=10)
            return pack_hits(hits, max_chars=MAX_TOOL_OUTPUT) or "No commits matched."

        if name == "timeline":
            return self._timeline()

        if name == "list_branches":
            return self._branches()

        if name == "list_todos":
            prefix = args.get("path_prefix", "")
            hits = hybrid_search(self.store, None, "TODO FIXME HACK", kinds=["todo"], k=20)
            todos = [h.chunk for h in hits if h.chunk.ref_id.startswith(prefix)]
            if not todos:
                hits = self.store.all_chunks(kinds=["todo"])
                todos = [c for _, c in hits if c.ref_id.startswith(prefix)][:20]
            return "\n".join(f"- {t.ref_id}: {t.content}" for t in todos) or "No TODO markers found."

        raise ValueError(f"unknown tool {name!r}")

    # -- higher-level summaries --------------------------------------------------
    def _timeline(self) -> str:
        commits = hybrid_search(self.store, None, "commit", kinds=["commit"], k=400)
        from collections import Counter

        months: Counter[str] = Counter()
        authors: Counter[str] = Counter()
        for h in commits:
            m = h.chunk.meta.get("date", "")[:7]
            months[m] += 1
            authors[h.chunk.meta.get("author", "?")] += 1
        cadence = "\n".join(f"  {m}: {'▇' * min(n, 40)} {n}" for m, n in sorted(months.items()))
        roster = ", ".join(f"{a} ({n})" for a, n in authors.most_common(8))
        return f"Monthly commit cadence:\n{cadence}\n\nAuthors: {roster}"

    def _branches(self) -> str:
        try:
            import pygit2

            from ..ingest.git_history import list_branch_tips

            repo = pygit2.Repository(self.repo_path)
            lines = []
            for name, target in list_branch_tips(repo):
                tip = repo[target]
                if not isinstance(tip, pygit2.Commit):
                    continue
                summary = tip.message.strip().splitlines()[0][:80]
                lines.append(f"- {name}: tip {str(target)[:7]} “{summary}”")
            return "\n".join(lines) or "No branches."
        except Exception as exc:
            return f"ERROR: {exc}"
