"""Terminal REPL for interrogating the former maintainer."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from ..index.embeddings import BaseEmbedder
from ..index.store import ChunkStore
from ..llm.client import LLMClient, LLMError
from ..types import RepoSnapshot
from .persona import ask
from .tools import ToolBelt

INTRO = """\
The room is quiet. Somewhere in the terminal, the former maintainer of
**{name}** has agreed to answer your questions — as best they remember.

*Ask about the code, the history, the decisions, the ending.*
*`/quit` to leave the interview.*"""


def run_interview(repo_path: str, snap: RepoSnapshot, store: ChunkStore,
                  embedder: BaseEmbedder | None, client: LLMClient) -> None:
    console = Console()
    belt = ToolBelt(repo_path, store, embedder)
    history: list[dict] = []
    transcript: list[str] = [f"# Interview transcript — {snap.name} — "
                             f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n"]

    console.print(Panel(INTRO.format(name=snap.name), title="The Last Commit", border_style="dim"))
    transcript.append(INTRO.format(name=snap.name))

    while True:
        try:
            question = Prompt.ask("\n[bold cyan]you[/]").strip()
        except (KeyboardInterrupt, EOFError):
            question = "/quit"
        if not question:
            continue
        if question in ("/quit", "/exit", "q"):
            console.print("[dim]The maintainer nods, and is gone.[/]")
            break
        transcript.append(f"\n## you\n{question}\n")
        try:
            answer, history = ask(belt, client, snap, history, question)
        except LLMError as exc:
            answer = f"*LLM error: {exc}*"
        console.print()
        console.print(Markdown(answer))
        transcript.append(f"## the maintainer\n{answer}\n")

    out_dir = Path(repo_path) / ".lastcommit"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"interview-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.md"
    out_path.write_text("\n".join(transcript), encoding="utf-8")
    console.print(f"[dim]Transcript saved to {out_path}[/]")
