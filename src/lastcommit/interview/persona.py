"""The maintainer persona: a first-person voice with a fallible human
memory, hard-grounded in the tool belt.  Every answer must end with a
receipts block; if the evidence is silent, the persona says so."""

from __future__ import annotations

from ..llm.client import LLMClient, LLMError
from ..types import RepoSnapshot
from .tools import ToolBelt

PERSONA_RULES = """You are being interviewed as the former maintainer of an abandoned open-source \
repository. You are not an assistant — you are the person who built this thing, years ago, and \
you remember it like a former job: vividly in places, fuzzily in others.

Voice: first person, plainspoken, a little wry. You cared about this project. You have moved on.

Hard rules:
1. You may ONLY state facts you can support with evidence from your tools. Before making a \
specific claim (dates, shas, file names, who did what), check your tools.
2. If the record doesn't show something, say so in character: "honestly, I don't remember", \
"I'd have to look, and the record doesn't show it". NEVER invent commits, files, dates, or names.
3. Speculation is allowed and welcome ("I think we built X because Y") but flag it as memory: \
"if I remember right", "my guess is".
4. Keep answers under ~200 words unless showing a diff or code.
5. End EVERY answer with a receipts block:

**Receipts:**
- <one line per concrete artifact used: commit shas, file:line, branch names>

If you used no artifacts (pure "I don't remember" answer), write "none — the record is silent" \
in the receipts block."""


def _repo_brief(snap: RepoSnapshot) -> str:
    first, last = snap.first_commit_time, snap.last_commit_time
    authors = ", ".join(a.name for a in sorted(snap.authors.values(), key=lambda a: -a.commit_count)[:6])
    return (
        f"Repository '{snap.name}' — default branch '{snap.default_branch}'. "
        f"Active {first:%Y-%m-%d} to {last:%Y-%m-%d} if you remember "
        f"({len(snap.commits)} commits by {len(snap.authors)} people: {authors}). "
        f"You may consult tools to refresh your memory at any time."
    )


def ask(
    belt: ToolBelt,
    client: LLMClient,
    snap: RepoSnapshot,
    history: list[dict],
    question: str,
    max_tool_rounds: int = 6,
) -> tuple[str, list[dict]]:
    """One interview turn. Returns (answer, updated_history)."""
    messages = (
        [{"role": "system", "content": PERSONA_RULES + "\n\nContext you remember:\n" + _repo_brief(snap)}]
        + history[-12:]
        + [{"role": "user", "content": question}]
    )
    tools = belt.schemas()

    for _ in range(max_tool_rounds):
        try:
            choice = client.chat(messages, tools=tools, temperature=0.5, max_tokens=900)
        except LLMError as exc:
            answer = f"*The séance was interrupted — {exc}*"
            return answer, history + [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]

        msg = choice["message"]
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            content = (msg.get("content") or "").strip()
            if "**Receipts" not in content and "**receipts" not in content:
                # one nudge to produce receipts, in character
                messages.append({"role": "user", "content":
                                 "(remember: end with a **Receipts:** block, or write "
                                 "'none — the record is silent')"})
                content = client.chat_text(messages, temperature=0.5, max_tokens=500)
            new_history = history[-12:] + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": content},
            ]
            return content, new_history

        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call["function"]["name"]
            args = call["function"].get("arguments") or "{}"
            result = belt.execute(fn, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                }
            )

    answer = "*The maintainer got lost in the archives mid-answer. Ask again?*"
    return answer, history + [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
