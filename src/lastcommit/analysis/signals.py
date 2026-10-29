"""Deterministic forensic detectors.

Each detector scans the `RepoSnapshot` and emits `Finding`s with typed
evidence.  No LLM involvement: these are the facts.  The narrative layer
may only dress these facts in prose, citing finding IDs.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from ..types import Finding, RepoSnapshot

WIP_RE = re.compile(
    r"^(wip|tmp|temp|temp commit|asdf|qwerty|fix later|todo|stuff|misc|oops|draft|test|save|commit)\b|\.\.\.$",
    re.IGNORECASE,
)
REGRET_RE = re.compile(
    r"revert|rollback|roll back|back ?out|broken|breaks|doesn'?t work|does not work|"
    r"don'?t use|do not use|oops|ugh|whoops|hotfix|hack(ed)? (around|together)|temp(orary)? fix",
    re.IGNORECASE,
)
REVERT_RE = re.compile(r"^\s*(revert|back ?out|rollback)\b[\"': ]*", re.IGNORECASE)
DEP_RE = re.compile(r"bump|upgrade|downgrade|switch(ing)? to|migrate|drop support|remove|dependency|requirements", re.IGNORECASE)
DOC_PATH_RE = re.compile(r"\.(md|rst|txt)$|^docs/|^(readme|changelog|contributing|news)", re.IGNORECASE)
MANIFEST_RE = re.compile(r"pyproject\.toml|setup\.(py|cfg)$|requirements.*\.txt|package\.json|Cargo\.toml|go\.mod|Gemfile")
TEST_PATH_RE = re.compile(r"(^|/)(tests?/|.*_test\.|test_.*\.|.*\.test\.|.*\.spec\.)", re.IGNORECASE)


def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


PRETTY_LANG = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript",
               ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby",
               ".c": "C", ".h": "C", ".cpp": "C++", ".sh": "Shell", ".md": "Markdown"}


def _active_window(commits) -> timedelta | None:
    if not commits:
        return None
    return max(c.author_time for c in commits) - min(c.author_time for c in commits)


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def detect_identity(snap: RepoSnapshot) -> list[Finding]:
    findings: list[Finding] = []
    first, last = snap.first_commit_time, snap.last_commit_time
    total_loc = sum(v for k, v in snap.languages.items() if k != ".md")

    if first and last:
        findings.append(
            Finding(
                category="identity",
                title=f"{snap.name}: born {first.strftime('%B %Y')}, silent since {last.strftime('%B %Y')}",
                description=(
                    f"The repository spans {_fmt_date(first)} to {_fmt_date(last)} "
                    f"({len(snap.commits)} commits, ~{total_loc:,} lines of code)."
                ),
                evidence=[
                    {"kind": "commit", "ref": c.short_sha,
                     "label": f"{_fmt_date(c.author_time)} — “{c.summary}” — earliest known commit"}
                    for c in sorted(snap.commits, key=lambda c: c.author_time)[:1]
                ]
                + [
                    {"kind": "commit", "ref": c.short_sha,
                     "label": f"{_fmt_date(c.author_time)} — “{c.summary}” — final known commit"}
                    for c in sorted(snap.commits, key=lambda c: c.author_time)[-1:]
                ],
                data={"first": _fmt_date(first), "last": _fmt_date(last),
                      "commits": len(snap.commits), "loc": total_loc},
            )
        )

    by_lines = sorted(snap.languages.items(), key=lambda kv: -kv[1])
    stack: list[str] = []
    for ext, _ in by_lines:
        pretty = PRETTY_LANG.get(ext, ext.lstrip("."))
        if pretty not in stack:
            stack.append(pretty)
    if stack:
        lang_line = ", ".join(f"{PRETTY_LANG.get(k, k.lstrip('.'))} ({v:,} lines)"
                              for k, v in by_lines[:6])
        findings.append(
            Finding(
                category="identity",
                title=f"Built with {', '.join(stack[:5])}",
                description=f"Language mix by line count: {lang_line}."
                            + (f" Manifests present: {', '.join(sorted(snap.manifests))}." if snap.manifests else ""),
                evidence=[{"kind": "doc", "ref": m, "label": f"dependency/build manifest {m}"} for m in snap.manifests],
                severity=1,
                data={"stack": stack, "languages": snap.languages},
            )
        )

    readme_blurb = _readme_blurb(snap)
    if readme_blurb:
        findings.append(
            Finding(
                category="identity",
                title="What it claimed to be",
                description=f"From the README: “{readme_blurb}”",
                evidence=[{"kind": "doc", "ref": "README", "label": "project self-description", "quote": readme_blurb}],
                severity=1,
            )
        )

    if snap.authors:
        roster = ", ".join(
            f"{a.name} ({a.commit_count} commits, last {_fmt_date(a.last_commit)})"
            for a in sorted(snap.authors.values(), key=lambda a: -a.commit_count)[:6]
        )
        findings.append(
            Finding(
                category="identity",
                title=f"The crew: {len(snap.authors)} contributor(s)",
                description=roster,
                evidence=[
                    {"kind": "author", "ref": a.name,
                     "label": f"active {_fmt_date(a.first_commit)} → {_fmt_date(a.last_commit)}, "
                              f"{a.commit_count} commits, +{a.insertions}/−{a.deletions}"}
                    for a in sorted(snap.authors.values(), key=lambda a: -a.commit_count)[:6]
                ],
                severity=1,
            )
        )
    return findings


def _readme_blurb(snap: RepoSnapshot) -> str:
    lines = snap.readme_text.splitlines()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!") or s.startswith("[!") or s.startswith("<"):
            continue
        if re.match(r"^[-*|=`\[\]]", s) or s.startswith("```"):
            continue
        return s[:280]
    return ""


# --------------------------------------------------------------------------
# Timeline of decline
# --------------------------------------------------------------------------


def monthly_cadence(snap: RepoSnapshot) -> dict[str, int]:
    months = Counter(c.author_time.strftime("%Y-%m") for c in snap.commits)
    return dict(sorted(months.items()))


def detect_timeline(snap: RepoSnapshot) -> list[Finding]:
    findings: list[Finding] = []
    if not snap.commits:
        return findings
    cadence = monthly_cadence(snap)
    months = sorted(cadence)

    # cadence collapse: last 3 active months vs best 3
    counts = sorted(cadence.values(), reverse=True)
    active_months = [m for m in months if cadence[m] > 0]
    last3 = sum(cadence[m] for m in active_months[-3:])
    best3 = sum(counts[:3])
    last_active = snap.last_commit_time
    newest = max(snap.commits, key=lambda c: c.committer_time)

    findings.append(
        Finding(
            category="timeline",
            title=f"The last commit landed on {_fmt_date(last_active)}",
            description=(
                f"“{newest.summary}” by {newest.author_name}. "
                f"Nothing has been committed since. Peak activity saw {best3} commits across three months; "
                f"the final three active months saw {last3}."
            ),
            evidence=[
                {"kind": "commit", "ref": newest.short_sha,
                 "label": f"final commit by {newest.author_name} on {_fmt_date(last_active)}",
                 "quote": newest.summary},
            ],
            severity=3,
            data={"cadence": cadence, "last_active_months": active_months[-3:], "best3": best3, "last3": last3},
        )
    )

    # author dropout order
    ordered = sorted(snap.authors.values(), key=lambda a: a.last_commit)
    if len(ordered) >= 2:
        dropout = "; ".join(f"{a.name} went quiet on {_fmt_date(a.last_commit)}" for a in ordered)
        findings.append(
            Finding(
                category="timeline",
                title="The crew stopped showing up one by one",
                description=dropout + ".",
                evidence=[
                    {"kind": "author", "ref": a.name, "label": f"last commit {_fmt_date(a.last_commit)} "
                                                               f"({a.commit_count} commits total)"}
                    for a in ordered
                ],
                severity=2,
            )
        )

    # death-bed files: most churned in the final 90 days of activity
    window_start = last_active - timedelta(days=90)
    recent = [f for f in snap.file_stats.items() if f[1]["last_touched"] >= window_start]
    death_bed = sorted(recent, key=lambda kv: -(kv[1]["insertions"] + kv[1]["deletions"]))[:5]
    if death_bed:
        findings.append(
            Finding(
                category="timeline",
                title="Where the work was when the lights went out",
                description="Most-churned files in the final 90 days — the natural place to resume:",
                evidence=[
                    {"kind": "code", "ref": path,
                     "label": f"{_plural(stats['commits'], 'commit')}, +{stats['insertions']}/−{stats['deletions']}, "
                              f"last touched {_fmt_date(stats['last_touched'])}"}
                    for path, stats in death_bed
                ],
                severity=1,
                data={"death_bed": [p for p, _ in death_bed]},
            )
        )
    return findings


# --------------------------------------------------------------------------
# Unfinished work
# --------------------------------------------------------------------------


def detect_unfinished(snap: RepoSnapshot) -> list[Finding]:
    findings: list[Finding] = []

    # stubs -----------------------------------------------------------------
    stubs = [s for s in snap.symbols if s.is_stub and s.stub_reason != "never referenced elsewhere (candidate dead code)"]
    if stubs:
        ev = [
            {"kind": "code", "ref": f"{s.path}:{s.start_line}-{s.end_line}",
             "label": f"{s.kind} `{s.name}` — {s.stub_reason}"}
            for s in stubs[:12]
        ]
        findings.append(
            Finding(
                category="unfinished",
                title=f"{len(stubs)} stub symbol(s) never fleshed out",
                description="Functions and methods left as placeholders in the final state of the code.",
                evidence=ev,
                severity=2,
                data={"stubs": [{"name": s.name, "path": s.path, "line": s.start_line, "reason": s.stub_reason}
                                for s in stubs]},
            )
        )

    # TODO clusters ----------------------------------------------------------
    if snap.todos:
        by_dir = Counter("/".join(t.path.split("/")[:-1]) or "." for t in snap.todos)
        top_dirs = ", ".join(f"`{d}/` ({n})" for d, n in by_dir.most_common(4))
        findings.append(
            Finding(
                category="unfinished",
                title=f"{len(snap.todos)} TODO/FIXME/HACK markers in the final tree",
                description=f"Clustered in: {top_dirs}. A sample of the loudest:",
                evidence=[
                    {"kind": "todo", "ref": f"{t.path}:{t.line}",
                     "label": f"{t.kind}: {t.text}", "quote": t.text}
                    for t in snap.todos[:12]
                ],
                severity=2,
                data={"todo_dirs": dict(by_dir)},
            )
        )

    # dangling branches -------------------------------------------------------
    dangling = [b for b in snap.branches if not b.merged and b.name != snap.default_branch]
    if dangling:
        findings.append(
            Finding(
                category="unfinished",
                title=f"{len(dangling)} branch(es) never merged — work stranded on them",
                description="These branch tips contain commits that exist nowhere on "
                            f"{snap.default_branch}:",
                evidence=[
                    {"kind": "branch", "ref": b.name,
                     "label": f"tip {b.tip_sha[:7]} ({_fmt_date(b.tip_time)}): “{b.tip_summary}” — {b.commit_count} commits"}
                    for b in dangling[:8]
                ],
                severity=2,
                data={"dangling": [b.name for b in dangling]},
            )
        )

    # WIP commits --------------------------------------------------------------
    wip = [c for c in snap.commits if WIP_RE.search(c.summary) or len(c.summary.strip()) <= 3]
    if wip:
        findings.append(
            Finding(
                category="unfinished",
                title=f"{len(wip)} commit(s) that look like [WIP]",
                description="Message patterns suggest unfinished work committed mid-stream:",
                evidence=[
                    {"kind": "commit", "ref": c.short_sha,
                     "label": f"{_fmt_date(c.author_time)} — “{c.summary}” — {c.author_name}"}
                    for c in wip[:10]
                ],
                severity=1,
                data={"wip": [c.short_sha for c in wip]},
            )
        )

    # dead code candidates ------------------------------------------------------
    dead = [s for s in snap.symbols if s.stub_reason == "never referenced elsewhere (candidate dead code)"]
    if dead:
        findings.append(
            Finding(
                category="unfinished",
                title=f"{len(dead)} symbol(s) with no remaining call sites",
                description="Defined but never referenced anywhere in the final tree — candidates for dead code "
                            "(or for features whose callers were removed):",
                evidence=[
                    {"kind": "code", "ref": f"{s.path}:{s.start_line}-{s.end_line}",
                     "label": f"{s.kind} `{s.name}`"}
                    for s in dead[:10]
                ],
                severity=1,
                data={"dead": [s.name for s in dead]},
            )
        )

    # changelog promises ---------------------------------------------------------
    unreleased = _unreleased_section(snap.changelog_text)
    if unreleased:
        heading, body = unreleased
        findings.append(
            Finding(
                category="unfinished",
                title=f"The CHANGELOG promised “{heading}” — it never shipped",
                description="Items recorded as unreleased/planned in the changelog:",
                evidence=[{"kind": "doc", "ref": "CHANGELOG", "label": f"section “{heading}”", "quote": body[:400]}],
                severity=2,
            )
        )
    return findings


def _unreleased_section(changelog: str) -> tuple[str, str] | None:
    if not changelog:
        return None
    lines = changelog.splitlines()
    heading_re = re.compile(r"^#{1,3}\s+(.*)$")
    for i, line in enumerate(lines):
        m = heading_re.match(line)
        if m and re.search(r"unreleased|planned|upcoming|next release|roadmap", m.group(1), re.IGNORECASE):
            body: list[str] = []
            for later in lines[i + 1 :]:
                if heading_re.match(later):
                    break
                body.append(later)
            text = "\n".join(body).strip()
            if text:
                return m.group(1).strip(), text
    return None


# --------------------------------------------------------------------------
# Suspicious regressions
# --------------------------------------------------------------------------


def detect_regressions(snap: RepoSnapshot) -> list[Finding]:
    findings: list[Finding] = []
    if not snap.commits:
        return findings
    oldest = min(c.committer_time for c in snap.commits)

    # revert events ---------------------------------------------------------
    reverts = [c for c in snap.commits if REVERT_RE.match(c.summary)]
    for c in reverts[:5]:
        original = _find_revert_original(snap, c)
        ev = [{"kind": "commit", "ref": c.short_sha,
               "label": f"{_fmt_date(c.author_time)} — “{c.summary}”", "quote": c.summary}]
        if original:
            ev.append({"kind": "commit", "ref": original.short_sha,
                       "label": f"likely the commit that was undone ({_fmt_date(original.author_time)}: "
                                f"“{original.summary}”, same files, inverted +/-)"})
        findings.append(
            Finding(
                category="regression",
                title=f"A change was deliberately undone: “{c.summary}”",
                description=("The commit history contains an explicit revert. Whatever it removed was once "
                             "considered worth building — and later considered wrong."),
                evidence=ev,
                severity=3,
                data={"reverted_by": c.short_sha, "reverted": original.short_sha if original else None},
            )
        )

    # regret-pattern messages ---------------------------------------------------
    regrets = [c for c in snap.commits if REGRET_RE.search(c.summary) and not REVERT_RE.match(c.summary)]
    if regrets:
        findings.append(
            Finding(
                category="regression",
                title=f"{len(regrets)} commit message(s) that smell of regret",
                description="Messages containing rollback/broken/does-not-work vocabulary:",
                evidence=[
                    {"kind": "commit", "ref": c.short_sha,
                     "label": f"{_fmt_date(c.author_time)} — “{c.summary}”", "quote": c.summary}
                    for c in regrets[:10]
                ],
                severity=2,
            )
        )

    # test disabling --------------------------------------------------------------
    test_commits = [c for c in snap.commits if any(TEST_PATH_RE.search(f.path) for f in c.files)
                    and re.search(r"skip|disable|xfail|broken|ci|flake", c.summary, re.IGNORECASE)]
    if test_commits:
        findings.append(
            Finding(
                category="regression",
                title=f"Tests were being skipped/disabled in {len(test_commits)} commit(s)",
                description="Commits touching test files with skip/disable vocabulary — the suite may have "
                            "been red when the project died:",
                evidence=[
                    {"kind": "commit", "ref": c.short_sha,
                     "label": f"{_fmt_date(c.author_time)} — “{c.summary}” — touched: "
                              + ", ".join(f.path for f in c.files if TEST_PATH_RE.search(f.path))[:150]}
                    for c in test_commits[:8]
                ],
                severity=2,
            )
        )

    # churn then silence ----------------------------------------------------------
    if snap.last_commit_time:
        horizon = snap.last_commit_time - timedelta(days=90)
        churny = sorted(snap.file_stats.items(), key=lambda kv: -(kv[1]["insertions"] + kv[1]["deletions"]))[:8]
        abandoned_files = [
            (p, st) for p, st in churny
            if st["last_touched"] < horizon and (st["last_touched"] - oldest) > timedelta(days=30)
        ]
        if abandoned_files:
            findings.append(
                Finding(
                    category="regression",
                    title="High-churn files that fell silent long before the end",
                    description="Files that absorbed enormous change, then stopped changing well before "
                                "the final commit — possible regressions nobody returned to:",
                    evidence=[
                        {"kind": "code", "ref": p,
                         "label": f"+{st['insertions']}/−{st['deletions']} over {_plural(st['commits'], 'commit')}, "
                                  f"last touched {_fmt_date(st['last_touched'])}"}
                        for p, st in abandoned_files[:6]
                    ],
                    severity=2,
                )
            )
    return findings


def _find_revert_original(snap: RepoSnapshot, revert_commit) -> object | None:
    """Find the likely original commit undone by a revert: same file set,
    inverted insertion/deletion counts."""
    r_paths = {f.path for f in revert_commit.files}
    best, best_score = None, 0
    for c in snap.commits:
        if c.sha == revert_commit.sha or c.author_time >= revert_commit.author_time:
            continue
        paths = {f.path for f in c.files}
        overlap = len(paths & r_paths) / max(len(paths | r_paths), 1)
        inverted = (c.insertions and abs(c.insertions - revert_commit.deletions) <= max(4, 0.2 * c.insertions)
                    and abs(c.deletions - revert_commit.insertions) <= max(4, 0.2 * c.deletions))
        score = overlap + (0.5 if inverted else 0)
        if overlap > 0.6 and score > best_score:
            best, best_score = c, score
    return best


# --------------------------------------------------------------------------
# Undocumented design decisions
# --------------------------------------------------------------------------


def detect_decisions(snap: RepoSnapshot) -> list[Finding]:
    findings: list[Finding] = []
    if not snap.commits:
        return findings

    # big refactors with no doc traffic nearby ---------------------------------
    doc_times = [
        c.committer_time for c in snap.commits if any(DOC_PATH_RE.search(f.path) for f in c.files)
    ]
    big = [c for c in snap.commits if len(c.files) >= 8 or (c.insertions + c.deletions) >= 300]
    for c in big[:40]:
        window = [t for t in doc_times if abs((t - c.committer_time).total_seconds()) < 14 * 86400]
        if window:
            continue
        findings.append(
            Finding(
                category="decision",
                title=f"“{c.summary[:60]}” reshaped {len(c.files)} files — no documentation was touched",
                description=(
                    f"{_fmt_date(c.author_time)}: +{c.insertions}/−{c.deletions} across {len(c.files)} files. "
                    "No README/docs/CHANGELOG commit within ±14 days, so the rationale lives (if anywhere) "
                    "only in this commit."
                ),
                evidence=[
                    {"kind": "commit", "ref": c.short_sha,
                     "label": f"{_fmt_date(c.author_time)} — “{c.summary}” by {c.author_name}",
                     "quote": c.message[:400]},
                ],
                severity=2,
            )
        )
        if len([f for f in findings if f.category == "decision"]) >= 3:
            break

    # dependency surface changes -------------------------------------------------
    dep_commits = [c for c in snap.commits if any(MANIFEST_RE.search(f.path) for f in c.files)]
    if dep_commits:
        findings.append(
            Finding(
                category="decision",
                title=f"The dependency surface was reshaped {len(dep_commits)} time(s)",
                description="Commits touching build/dependency manifests — stack changes are design decisions "
                            "rarely written down:",
                evidence=[
                    {"kind": "commit", "ref": c.short_sha,
                     "label": f"{_fmt_date(c.author_time)} — “{c.summary}” — "
                              + ", ".join(sorted({f.path for f in c.files if MANIFEST_RE.search(f.path)}))}
                    for c in dep_commits[:8]
                ],
                severity=1,
            )
        )
    return findings


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

DETECTORS = [detect_identity, detect_timeline, detect_unfinished, detect_regressions, detect_decisions]


def run_all_detectors(snap: RepoSnapshot) -> list[Finding]:
    findings: list[Finding] = []
    for detector in DETECTORS:
        try:
            findings.extend(detector(snap))
        except Exception as exc:  # a detector must never kill the report
            findings.append(
                Finding(
                    category="timeline",
                    title=f"Detector {detector.__name__} failed",
                    description=f"Internal error while scanning: {exc!r}",
                    severity=1,
                )
            )
    for i, f in enumerate(findings, start=1):
        f.id = f"F-{i:03d}"
        f.evidence = [{"kind": e["kind"], "ref": e["ref"], "label": e["label"], "quote": e.get("quote")}
                      for e in f.evidence]
    return findings
