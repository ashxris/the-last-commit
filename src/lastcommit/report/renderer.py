"""Markdown report renderer. Deterministic sections are always present;
LLM narrative (pre-verified) is layered on top when available."""

from __future__ import annotations

from datetime import datetime, timezone

from ..types import Finding, RepoSnapshot
from ..analysis.signals import monthly_cadence, PRETTY_LANG

SECTIONS: list[tuple[str, str]] = [
    ("identity", "Case File — What Was This?"),
    ("timeline", "Timeline of Decline"),
    ("unfinished", "The Autopsy — Unfinished Work"),
    ("regression", "Anomalies & Suspicious Regressions"),
    ("decision", "Decisions Nobody Wrote Down"),
    ("roadmap", "Revival Roadmap"),
]

_SEVERITY_TAG = {1: "note", 2: "notable", 3: "alarming"}


def _summary_section(snap: RepoSnapshot, findings: list[Finding], narratives: dict[str, str]) -> str:
    """Closing summary: verified LLM verdict (if any) over a deterministic digest."""
    out: list[str] = ["## Summary\n"]
    verdict = narratives.get("summary")
    if verdict:
        out.append(verdict + "\n")

    first, last = snap.first_commit_time, snap.last_commit_time
    if first and last:
        total_loc = sum(v for ext, v in snap.languages.items() if ext != ".md")
        by_lines = sorted(snap.languages.items(), key=lambda kv: -kv[1])
        stack = ", ".join(PRETTY_LANG.get(ext, ext.lstrip(".")) for ext, _ in by_lines[:3])
        sev3 = sum(1 for f in findings if f.severity == 3)
        sev2 = sum(1 for f in findings if f.severity == 2)
        out.append(
            f"`{snap.name}` was a {stack} project spanning {first:%B %Y} to {last:%B %Y} — "
            f"{len(snap.commits)} commits by {len(snap.authors)} author(s), ~{total_loc:,} lines of "
            f"code, silent since {last:%B %Y}. This scan recorded {len(findings)} findings: "
            f"{sev3} alarming, {sev2} notable."
        )

    headlines = sorted(findings, key=lambda f: -f.severity)[:6]
    if headlines:
        out.append("\n**The headline findings:**\n")
        for f in headlines:
            out.append(f"- **{f.title}** [{f.id}]")
        out.append("\n_Start below with the Revival Roadmap, or interrogate the former maintainer "
                   "with `lastcommit interview`._")
    elif not verdict:
        return ""  # nothing to say — omit the section
    return "\n".join(out)


def _render_finding(f: Finding) -> str:
    lines = [f"#### {f.title}", ""]
    tag = _SEVERITY_TAG.get(f.severity, "")
    if tag:
        lines[0] += f" `[{tag}]`"
    lines += [f.description, ""]
    if f.evidence:
        lines.append("**Evidence:**")
        lines.append("")
        for e in f.evidence[:8]:
            quote = f' — “{e["quote"]}”' if e.get("quote") else ""
            lines.append(f"- `{e['ref']}` {e['label']}{quote}")
        lines.append("")
    return "\n".join(lines)


def _cadence_table(snap: RepoSnapshot) -> str:
    cadence = monthly_cadence(snap)
    if not cadence:
        return ""
    rows = ["| Month | Commits |", "|---|---|"]
    for month, count in list(cadence.items())[-18:]:
        bar = "▇" * min(count, 30)
        rows.append(f"| {month} | {count} {bar} |")
    return "\n".join(rows) + "\n"


def _deterministic_roadmap(findings: list[Finding]) -> str:
    items = [f for f in findings if f.category in ("unfinished", "timeline") and f.evidence]
    if not items:
        return "_Not enough evidence to propose a roadmap._"
    lines: list[str] = []
    for i, f in enumerate(items[:5], start=1):
        first_ref = f.evidence[0]["ref"]
        lines.append(
            f"**{i}. Start from: {f.title}**\n"
            f"- Why: {f.description[:200]} [{f.id}]\n"
            f"- First step: inspect `{first_ref}` and decide whether to resume or remove.\n"
            f"- Risk: low — this is deterministic evidence, not speculation."
        )
    return "\n\n".join(lines)


def render_report(
    snap: RepoSnapshot,
    findings: list[Finding],
    narratives: dict[str, str],
    roadmap: str,
    llm_used: bool,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out: list[str] = []
    out.append(f"# The Last Commit — Forensic Report: `{snap.name}`\n")
    out.append(
        f"*Autopsied {now} · HEAD `{snap.head_sha[:12] or '(no commits)'}` · "
        f"branch `{snap.default_branch}` · {len(snap.commits)} commits · "
        f"{len(snap.authors)} author(s) · {'LLM narrative on' if llm_used else 'deterministic mode (no LLM)'}*\n"
    )

    for key, title in SECTIONS:
        if key == "roadmap":
            out.append(f"## {title}\n")
            body = roadmap.strip() or _deterministic_roadmap(findings)
            out.append(body + "\n")
            continue
        cat_findings = [f for f in findings if f.category == key]
        if not cat_findings:
            continue
        out.append(f"## {title}\n")
        narrative = narratives.get(key)
        if narrative:
            out.append(narrative + "\n")
        for f in cat_findings:
            out.append(_render_finding(f))
        if key == "timeline":
            table = _cadence_table(snap)
            if table:
                out.append("**Commit cadence (final months):**\n")
                out.append(table)

    summary = _summary_section(snap, findings, narratives)
    if summary:
        out.append(summary + "\n")

    out.append("## Method\n")
    out.append(
        "Findings were computed deterministically from the git history (pygit2), the final source "
        "tree (tree-sitter), and repository documentation. Narrative passages were generated by an "
        "open-weight model constrained to cite finding IDs, then machine-verified: every "
        "referenced finding and commit hash was checked against the evidence set. Claims that "
        "failed verification were stripped, not trusted.\n"
    )
    return "\n".join(out)
