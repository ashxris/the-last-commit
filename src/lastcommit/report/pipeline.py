"""Shared report-generation pipeline used by both the CLI and the web app.

One function, one ordering: ingest → detectors → (optional, verified)
narrative → render.  Progress is reported through a callback so each
surface can present stages its own way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..analysis import narrative as nar
from ..analysis.signals import run_all_detectors
from ..index.build import ingest_repository
from ..llm.client import LLMClient
from ..llm.config import load_config
from .renderer import render_report

ProgressFn = Callable[[str], None]


@dataclass
class AnalysisResult:
    markdown: str
    llm_used: bool
    repo_name: str
    commits: int
    findings: int
    embedder: str | None
    brief: dict  # identity facts the web interview uses as the persona's memory


def _snapshot_brief(snap) -> dict:
    first, last = snap.first_commit_time, snap.last_commit_time
    return {
        "name": snap.name,
        "branch": snap.default_branch,
        "first": first.strftime("%Y-%m-%d") if first else "?",
        "last": last.strftime("%Y-%m-%d") if last else "?",
        "commits": len(snap.commits),
        "authors": [a.name for a in sorted(snap.authors.values(), key=lambda a: -a.commit_count)[:8]],
    }


def generate_report(
    repo_path: str,
    no_llm: bool = False,
    progress: ProgressFn | None = None,
    repo_name: str | None = None,
) -> AnalysisResult:
    """Analyze the repo at `repo_path`.  `repo_name` overrides the display
    name (the web app passes the GitHub name; the CLI uses the folder name)."""
    def stage(name: str) -> None:
        if progress:
            progress(name)

    cfg = load_config()
    client = LLMClient(cfg.llm)
    llm_ok = False
    if not no_llm:
        stage("llm-check")
        llm_ok = client.available()

    stage("ingest")
    snap, store, embedder = ingest_repository(str(repo_path), cfg.embed)
    if repo_name:
        snap.name = repo_name
    stage("scan")
    findings = run_all_detectors(snap)

    narratives: dict[str, str] = {}
    roadmap = ""
    llm_used = False
    if llm_ok and findings:
        stage("narrate")
        try:
            narratives = nar.synthesize_narrative(snap, findings, client)
            narratives["summary"] = nar.synthesize_summary(snap, findings, client)
            narratives = {k: v for k, v in narratives.items() if v}
            narratives = nar.verify_narrative(narratives, findings, snap)
            roadmap = nar.synthesize_roadmap(snap, findings, client)
        except nar.LLMError:
            # An LLM outage, quota wall, or oversized request must never sink a
            # report — the deterministic sections stand on their own.
            narratives, roadmap = {}, ""
        llm_used = bool(narratives or roadmap)

    stage("render")
    report = render_report(snap, findings, narratives, roadmap, llm_used)
    return AnalysisResult(
        markdown=report,
        llm_used=llm_used,
        repo_name=snap.name,
        commits=len(snap.commits),
        findings=len(findings),
        embedder=embedder.name if embedder else None,
        brief=_snapshot_brief(snap),
    )
