"""Narrative synthesis: the LLM's only job is prose and hypothesis.

Facts come from the deterministic detectors; the model writes the story
and proposes *plausible intent* — and every claim must cite a finding ID.
The output is run through the verifier before it reaches the report.
"""

from __future__ import annotations

import json

from ..llm.client import LLMClient, LLMError
from ..types import Finding, RepoSnapshot
from .verify import verify_narrative

GROUND_RULES = """You are a forensic software archaeologist writing a report on an abandoned \
repository. The findings below were computed deterministically from the repository's git history, \
AST, and docs — they are FACTS. Your prose may interpret and connect them, but you must obey:

1. Cite findings by ID in square brackets, e.g. [F-003]. Never invent new IDs.
2. Never invent commit hashes, file paths, dates, or names. Only use identifiers that appear \
in the findings.
3. You may propose hypotheses about intent (the interesting part!) but mark them clearly with \
"likely", "probably", or "the evidence suggests".
4. Write in plain, evocative but precise prose. 2-3 short paragraphs maximum.
5. Do not use headings; the renderer supplies them."""


def _findings_json(findings: list[Finding]) -> str:
    return json.dumps(
        [
            {
                "id": f.id,
                "title": f.title,
                "description": f.description,
                "severity": f.severity,
                "evidence": [{"ref": e["ref"], "label": e["label"]} for e in f.evidence[:6]],
            }
            for f in findings
        ],
        indent=1,
    )


SECTION_BRIEFS = {
    "identity": (
        "Identity & Cause of Death",
        "Introduce this project: what it was trying to be, who built it, and when it died. "
        "Read between the lines of the README claims versus what the final commits show.",
    ),
    "timeline": (
        "Timeline of Decline",
        "Narrate how the project wound down: the rhythm of work, who dropped out and when, "
        "where the last effort was concentrated.",
    ),
    "unfinished": (
        "The Autopsy",
        "Describe the unfinished work: stubs, TODOs, stranded branches, WIP commits. "
        "Hypothesize what each cluster of unfinished work was *for*.",
    ),
    "regression": (
        "Anomalies & Regressions",
        "Describe what was undone, disabled, or abandoned mid-flight. These are the suspicious "
        "moments — speculate carefully about what went wrong.",
    ),
    "decision": (
        "Decisions Nobody Wrote Down",
        "Describe the large changes that reshaped the codebase without accompanying documentation. "
        "What design decisions can you infer from them?",
    ),
}


def synthesize_narrative(snap: RepoSnapshot, findings: list[Finding], client: LLMClient) -> dict[str, str]:
    sections: dict[str, str] = {}
    for key, (section_title, brief) in SECTION_BRIEFS.items():
        subset = [f for f in findings if f.category == key]
        if not subset:
            continue
        prompt = (
            f"{GROUND_RULES}\n\n## Section to write: {section_title}\n{brief}\n\n"
            f"## Repository: {snap.name}\n"
            f"Default branch: {snap.default_branch}. Final state analyzed at HEAD.\n\n"
            f"## Findings (facts):\n{_findings_json(subset)}\n\nWrite the section now."
        )
        try:
            text = client.chat_text(
                [{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=700,
            )
        except LLMError:
            continue  # section falls back to deterministic rendering
        if text and text.strip():
            sections[key] = text.strip()
    return sections


def synthesize_summary(snap: RepoSnapshot, findings: list[Finding], client: LLMClient) -> str:
    """The closing verdict paragraph for the Summary section."""
    prompt = (
        f"{GROUND_RULES}\n\n## Section to write: Closing Summary\n"
        "Write the verdict of the autopsy: 3-5 sentences covering what this project was trying "
        "to be, how and why it probably died, and the one thing a reviver should understand first. "
        "No headings, no bullet lists — a single tight paragraph.\n\n"
        f"## Repository: {snap.name} (default branch {snap.default_branch})\n\n"
        f"## Findings (facts):\n{_findings_json(findings)}\n\nWrite the summary now."
    )
    try:
        text = client.chat_text([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=400)
    except LLMError:
        return ""
    return text.strip()


ROADMAP_JSON_RULES = (
    "You are a forensic software archaeologist. The findings below are FACTS "
    "computed from a repository. Propose a revival roadmap. NEVER invent identifiers; cite finding IDs."
)


def synthesize_roadmap(snap: RepoSnapshot, findings: list[Finding], client: LLMClient) -> str:
    prompt = (
        f"{ROADMAP_JSON_RULES}\n\nRepository: {snap.name} (default branch {snap.default_branch}).\n\n"
        f"## Findings (facts):\n{_findings_json(findings)}\n\n"
        "Return ONLY a JSON array (no markdown fence) of at most 5 objects with keys: "
        "title, why, first_step, risk, cites (array of finding ids like \"F-001\"). "
        "Rank by revival value; prefer half-finished work the evidence points to."
    )
    import re as _re

    text = client.chat_text([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=1200)
    m = _re.search(r"\[.*\]", text, _re.DOTALL)
    if not m:
        return ""
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return ""
    from .verify import known_sha_set

    shas = known_sha_set(snap)
    valid_ids = {f.id for f in findings}
    lines: list[str] = []
    for i, item in enumerate(items[:5], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", f"Step {i}")).strip()
        why = str(item.get("why", "")).strip()
        step = str(item.get("first_step", "")).strip()
        risk = str(item.get("risk", "")).strip()
        for tok in set(_re.findall(r"\b[0-9a-f]{7,40}\b", f"{why} {step} {risk}")):
            if not any(s.startswith(tok.lower()) or tok.lower().startswith(s) for s in shas):
                why = why.replace(tok, "…")
                step = step.replace(tok, "…")
                risk = risk.replace(tok, "…")
        for ref in set(_re.findall(r"F-\d{1,4}", f"{why} {step} {risk}")):
            if ref not in valid_ids:
                why = why.replace(f"[{ref}]", "")
                step = step.replace(f"[{ref}]", "")
                risk = risk.replace(f"[{ref}]", "")
        lines.append(
            f"**{i}. {title}**\n"
            f"- Why: {why}\n"
            f"- First step: {step}\n"
            f"- Risk: {risk}"
        )
    return "\n\n".join(lines)
