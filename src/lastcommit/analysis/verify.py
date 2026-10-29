"""Citation verifier — the anti-hallucination spine.

The narrative LLM may only reference finding IDs ([F-007]) and identifiers
that exist in the deterministic evidence set.  Anything else — invented
shas, phantom finding refs — is stripped before the report ships.
"""

from __future__ import annotations

import re

from ..types import RepoSnapshot

FINDING_REF_RE = re.compile(r"\[(F-\d{1,4})\]")
HEX_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def known_sha_set(snap: RepoSnapshot) -> set[str]:
    shas: set[str] = set()
    for c in snap.commits:
        shas.add(c.sha.lower())
        shas.add(c.short_sha.lower())
        shas.add(c.sha[:8].lower())
    return shas


def _strip_hex_token(text: str, token: str, shas: set[str]) -> str:
    t = token.lower()
    if any(s.startswith(t) or t.startswith(s) for s in shas):
        return text
    return text.replace(token, "(unverified-ref-removed)")


def verify_text(text: str, findings: list, snap: RepoSnapshot) -> tuple[str, list[str]]:
    """Return (cleaned_text, list_of_removals)."""
    removals: list[str] = []
    valid_ids = {f.id for f in findings if f.id}

    def check_ref(m: re.Match) -> str:
        ref = m.group(1)
        if ref in valid_ids:
            return m.group(0)
        removals.append(f"dangling finding reference {ref}")
        return ""

    text = FINDING_REF_RE.sub(check_ref, text)

    shas = known_sha_set(snap)
    for token in set(HEX_RE.findall(text)):
        cleaned = _strip_hex_token(text, token, shas)
        if cleaned != text:
            removals.append(f"unverifiable hash {token}")
            text = cleaned
    return text, removals


def verify_narrative(sections: dict[str, str], findings: list, snap: RepoSnapshot) -> dict[str, str]:
    cleaned: dict[str, str] = {}
    for key, text in sections.items():
        clean, _ = verify_text(text, findings, snap)
        cleaned[key] = clean
    return cleaned
