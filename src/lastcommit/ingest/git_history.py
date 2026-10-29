"""Git history extraction via pygit2.

The one place that touches the git object database directly; everything
downstream consumes `RepoSnapshot` / `CommitInfo` records.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import pygit2

from ..types import AuthorInfo, BranchInfo, CommitInfo, FileChange, RepoSnapshot, utc

_STATUS = {
    pygit2.GIT_DELTA_ADDED: "A",
    pygit2.GIT_DELTA_MODIFIED: "M",
    pygit2.GIT_DELTA_DELETED: "D",
    pygit2.GIT_DELTA_RENAMED: "R",
    pygit2.GIT_DELTA_TYPECHANGE: "T",
}

CODE_EXTS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "c++",
    ".sh": "shell",
    ".md": "markdown",
}
MANIFEST_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "package.json", "Cargo.toml", "go.mod", "Gemfile",
}


def _open(path: str) -> pygit2.Repository:
    repo = pygit2.Repository(path)
    if repo.is_bare:
        pass  # bare repos are still analyzable
    return repo


def _default_branch(repo: pygit2.Repository) -> str:
    try:
        return repo.head.shorthand
    except (KeyError, pygit2.GitError):
        pass
    for cand in ("main", "master"):
        if cand in repo.branches.local:
            return cand
    return next(iter(repo.branches.local), "main")


def _head_sha(repo: pygit2.Repository) -> str:
    try:
        return str(repo.head.target)
    except (KeyError, pygit2.GitError):
        return ""


def _iter_local_branches(repo: pygit2.Repository):
    """(name, Branch) pairs across pygit2 versions."""
    for name in repo.branches.local:
        yield name, repo.branches.local[name]


def list_branch_tips(repo: pygit2.Repository) -> list[tuple[str, pygit2.Oid]]:
    """Every branch tip worth walking: local branches plus remote-tracking
    refs (a fresh clone only has one local branch, so remote refs are where
    the stranded work lives).  Deduped by commit — local names win."""
    tips: dict[pygit2.Oid, str] = {}
    for name, ref in _iter_local_branches(repo):
        tips.setdefault(ref.target, name)
    for ref_name in repo.references:
        if ref_name.startswith("refs/remotes/") and not ref_name.endswith("/HEAD"):
            try:
                target = repo.references[ref_name].target
            except (KeyError, pygit2.GitError):
                continue
            remote_name = ref_name.replace("refs/remotes/", "")
            tips.setdefault(target, remote_name)
    return [(name, target) for target, name in tips.items()]


def _all_tips(repo: pygit2.Repository) -> list[tuple[str, pygit2.Oid]]:
    """Every commit tip we know about: all branch tips + detached HEAD."""
    tips = {f"refs/heads/{name}": target for name, target in list_branch_tips(repo)}
    try:
        tips["HEAD"] = repo.head.target
    except (KeyError, pygit2.GitError):
        pass
    return list(tips.items())


def _commit_changes(repo: pygit2.Repository, commit: pygit2.Commit) -> tuple[list[FileChange], int, int]:
    try:
        if commit.parents:
            diff = repo.diff(commit.parents[0], commit)
        else:
            diff = commit.tree.diff_to_tree()  # root commit vs empty tree
        changes: list[FileChange] = []
        ins = dele = 0
        try:
            stats = diff.stats
            ins, dele = stats.insertions, stats.deletions
        except (KeyError, ValueError):
            pass
        # per-file line counts come from Patch.line_stats, not from deltas
        per_file: dict[str, tuple[int, int]] = {}
        for patch in diff:
            delta = patch.delta
            path = delta.new_file.path if delta.new_file.path else delta.old_file.path
            if path:
                try:
                    _ctx, added, removed = patch.line_stats
                    per_file[path] = (added, removed)
                except (KeyError, ValueError):
                    per_file[path] = (0, 0)
        for delta in diff.deltas:
            status = _STATUS.get(delta.status, "M")
            new_path = delta.new_file.path if delta.new_file.path else delta.old_file.path
            if not new_path:
                continue
            added, removed = per_file.get(new_path, (0, 0))
            changes.append(
                FileChange(
                    path=new_path,
                    status=status,
                    insertions=added,
                    deletions=removed,
                    old_path=delta.old_file.path if status == "R" else None,
                )
            )
        return changes, ins, dele
    except (pygit2.GitError, KeyError):
        return [], 0, 0


def extract_git_history(repo_path: str) -> RepoSnapshot:
    repo = _open(repo_path)
    default_branch = _default_branch(repo)
    head = _head_sha(repo)

    # ---- walk every tip, dedupe by sha (newest first) --------------------
    seen: set[str] = set()
    commits: list[CommitInfo] = []
    for _ref, tip in _all_tips(repo):
        try:
            walker = repo.walk(tip, pygit2.GIT_SORT_TOPOLOGICAL | pygit2.GIT_SORT_TIME)
        except (KeyError, pygit2.GitError):
            continue
        for c in walker:
            sha = str(c.id)
            if sha in seen:
                continue
            seen.add(sha)
            files, ins, dele = _commit_changes(repo, c)
            commits.append(
                CommitInfo(
                    sha=sha,
                    short_sha=sha[:7],
                    author_name=c.author.name or "unknown",
                    author_email=c.author.email or "",
                    author_time=utc(c.author.time),
                    committer_time=utc(c.committer.time),
                    summary=(c.message.strip().splitlines() or [""])[0][:120],
                    message=c.message.strip(),
                    parents=[str(p) for p in c.parent_ids],
                    is_merge=len(c.parent_ids) > 1,
                    files=files,
                    insertions=ins,
                    deletions=dele,
                )
            )
    commits.sort(key=lambda c: c.committer_time, reverse=True)

    # ---- authors ---------------------------------------------------------
    authors: dict[str, AuthorInfo] = {}
    for c in commits:
        a = authors.get(c.author_name)
        if a is None:
            authors[c.author_name] = AuthorInfo(c.author_name, c.author_email, c.author_time, c.author_time)
            a = authors[c.author_name]
        a.commit_count += 1
        a.insertions += c.insertions
        a.deletions += c.deletions
        a.first_commit = min(a.first_commit, c.author_time)
        a.last_commit = max(a.last_commit, c.author_time)

    # ---- branches --------------------------------------------------------
    reachable: set[str] = set()
    if head:
        for c in repo.walk(head, pygit2.GIT_SORT_TOPOLOGICAL):
            reachable.add(str(c.id))
    branches: list[BranchInfo] = []
    for name, target in list_branch_tips(repo):
        tip = repo[target]
        if not isinstance(tip, pygit2.Commit):
            continue
        count = sum(1 for _ in repo.walk(target, pygit2.GIT_SORT_TOPOLOGICAL))
        branches.append(
            BranchInfo(
                name=name,
                tip_sha=str(target),
                tip_summary=(tip.message.strip().splitlines() or [""])[0][:120],
                tip_time=utc(tip.committer.time),
                commit_count=count,
                merged=str(target) in reachable,
            )
        )
    branches.sort(key=lambda b: (b.merged, b.tip_time), reverse=False)

    tags = sorted(
        name.replace("refs/tags/", "")
        for name in repo.references
        if name.startswith("refs/tags/")
    )

    # ---- per-file churn stats --------------------------------------------
    file_stats: dict[str, dict] = defaultdict(lambda: {"commits": 0, "insertions": 0, "deletions": 0})
    for c in commits:
        for f in c.files:
            st = file_stats[f.path]
            st["commits"] += 1
            st["insertions"] += f.insertions
            st["deletions"] += f.deletions
            st.setdefault("first_touched", c.committer_time)
            st["first_touched"] = min(st["first_touched"], c.committer_time)
            st["last_touched"] = max(st.get("last_touched", c.committer_time), c.committer_time)
    file_stats = dict(file_stats)

    snap = RepoSnapshot(
        path=str(repo_path),
        name=repo_path.rstrip("/").split("/")[-1] or "repo",
        default_branch=default_branch,
        head_sha=head,
        commits=commits,
        authors=authors,
        branches=branches,
        tags=tags,
        file_stats=file_stats,
    )
    _count_languages(snap, repo)
    return snap


def _count_languages(snap: RepoSnapshot, repo: pygit2.Repository) -> None:
    try:
        tree = repo[repo.head.target].tree if repo.head.target else None
    except (KeyError, pygit2.GitError):
        return
    if tree is None:
        return
    lines: dict[str, int] = defaultdict(int)

    def visit(tree_obj: pygit2.Tree, prefix: str) -> None:
        for entry in tree_obj:
            name = prefix + entry.name
            if entry.type_str == "tree":
                visit(repo[entry.id], name + "/")
            else:
                ext = ("." + name.rsplit(".", 1)[-1]) if "." in name else ""
                if ext in CODE_EXTS and entry.type_str == "blob":
                    try:
                        text = repo[entry.id].data.decode("utf-8", errors="replace")
                        lines[ext] += text.count("\n") + 1
                    except (KeyError, UnicodeDecodeError):
                        continue

    visit(tree, "")
    snap.languages = dict(lines)


def read_blob_at(repo_path: str, rev: str, file_path: str) -> str:
    """Content of `file_path` at `rev` (sha, branch, tag, HEAD~N, ...)."""
    repo = _open(repo_path)
    commit = repo.revparse_single(rev)
    if not isinstance(commit, pygit2.Commit):
        raise ValueError(f"{rev!r} does not resolve to a commit")
    tree = commit.tree
    parts = [p for p in file_path.split("/") if p]
    obj = tree
    for part in parts:
        obj = obj[part]
    if obj.type_str != "blob":
        raise ValueError(f"{file_path!r} at {rev!r} is not a file")
    return repo[obj.id].data.decode("utf-8", errors="replace")


def blame_file(repo_path: str, file_path: str) -> list[tuple[int, str, str]]:
    """[(start_line, short_sha, author)] hunks, newest attribution per line."""
    repo = _open(repo_path)
    blame = repo.blame(file_path)
    out: list[tuple[int, str, str]] = []
    for hunk in blame:
        commit = repo[hunk.final_commit_id]
        out.append((hunk.final_start_line_number, str(hunk.final_commit_id)[:7], commit.author.name or "unknown"))
    return out


def show_commit(repo_path: str, sha: str, max_patch: int = 60) -> dict:
    """Commit metadata + a truncated textual diff for the interview tools."""
    repo = _open(repo_path)
    commit = repo.revparse_single(sha)
    if not isinstance(commit, pygit2.Commit):
        raise ValueError(f"{sha!r} is not a commit")
    if commit.parents:
        diff = repo.diff(commit.parents[0], commit)
    else:
        diff = commit.tree.diff_to_tree()
    patch = diff.text if hasattr(diff, "text") else ""
    lines = patch.splitlines()
    if len(lines) > max_patch:
        lines = lines[:max_patch] + [f"... ({len(lines) - max_patch} more diff lines)"]
    return {
        "sha": str(commit.id),
        "author": commit.author.name,
        "date": datetime.fromtimestamp(commit.committer.time, tz=timezone.utc).isoformat(),
        "message": commit.message.strip(),
        "patch": "\n".join(lines),
    }
