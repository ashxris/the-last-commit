"""Chat-with-the-document: the web interview.

A deliberately slim variant of the CLI séance.  Free-tier token budgets
(ITPM ~7k) mean every request must stay small, so: five terse tools,
tool results capped at ~1200 chars, three tool rounds maximum, six
messages of history, and the generated report riding along as the
maintainer's own memory.  Each question lands around 9-12k input tokens
across a handful of requests — the LLM client's retry-after pacing
absorbs the per-minute wall.
"""

from __future__ import annotations

from ..interview.tools import ToolBelt
from ..llm.client import LLMClient, LLMError

SLIM_RULES = """You are the former maintainer of an abandoned repository, being interviewed \
through a séance on a website. You are not an assistant — you built this thing, and you remember \
it like a former job: vividly in places, fuzzily in others.

Voice: first person, plainspoken, a little wry. You cared about this project. You have moved on.

Hard rules:
1. Ground specific claims (dates, shas, file names, who did what) in your tools or in the report \
excerpt you were given. Check tools before asserting specifics.
2. If the record doesn't show it, say so in character: "honestly, I don't remember", "the record \
doesn't show that". NEVER invent commits, files, dates, or names.
3. Speculate freely about intent, but flag it: "if I remember right", "my guess is".
4. Under ~150 words unless showing a diff or code.
5. End EVERY answer with a receipts block:

**Receipts:**
- <one line per artifact used: shas, file:line, branches>

If none, write "none — the record is silent"."""


MAX_REPORT_CHARS = 7000  # ≈1.8k tokens of the maintainer's own memory
MAX_ROUNDS = 3
MAX_HISTORY = 6


def _brief_text(brief: dict) -> str:
    return (
        f"Repository '{brief.get('name', '?')}' — default branch '{brief.get('branch', 'main')}'. "
        f"Active {brief.get('first', '?')} to {brief.get('last', '?')}; "
        f"{brief.get('commits', '?')} commits by {len(brief.get('authors', []))} people "
        f"({', '.join(brief.get('authors', [])[:6])}). "
        "You may consult tools to refresh your memory at any time."
    )


def chat_turn(
    belt: ToolBelt,
    client: LLMClient,
    brief: dict,
    report_markdown: str,
    history: list[dict],
    question: str,
) -> tuple[str, list[dict]]:
    """One web interview turn. Returns (answer, new_history)."""
    system = (
        f"{SLIM_RULES}\n\nContext you remember:\n{_brief_text(brief)}\n\n"
        "The forensic report written about your own repository (your memory, externalized):\n\n"
        f"{report_markdown[:MAX_REPORT_CHARS]}"
    )
    messages = [{"role": "system", "content": system}] + history[-MAX_HISTORY:] + [
        {"role": "user", "content": question}
    ]
    tools = belt.schemas()

    for _ in range(MAX_ROUNDS):
        try:
            choice = client.chat(messages, tools=tools, temperature=0.5, max_tokens=700)
        except LLMError as exc:
            answer = f"*The connection to the other side faltered — {exc}*"
            return answer, history + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ]
        msg = choice["message"]
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            content = (msg.get("content") or "").strip()
            if "**Receipts" not in content and "**receipts" not in content:
                messages.append({"role": "user", "content": "(remember: end with a **Receipts:** "
                                                          "block, or write 'none — the record is silent')"})
                try:
                    content = client.chat_text(messages, temperature=0.5, max_tokens=400)
                except LLMError:
                    pass  # keep the unreceipted answer rather than losing the turn
            new_history = history[-MAX_HISTORY:] + [
                {"role": "user", "content": question},
                {"role": "assistant", "content": content},
            ]
            return content, new_history
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        for call in tool_calls:
            result = belt.execute(call["function"]["name"], call["function"].get("arguments") or "{}")
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})

    answer = "*The maintainer got lost in the archives mid-answer. Ask again?*"
    return answer, history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": answer},
    ]
