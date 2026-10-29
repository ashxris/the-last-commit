"""The Last Commit — command line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(
    name="lastcommit",
    help="An AI archaeologist for abandoned repositories.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_STAGE_LABELS = {
    "llm-check": "reaching for the model host",
    "ingest": "excavating the repository",
    "scan": "running the forensic scan",
    "narrate": "the model is reading the evidence",
    "render": "composing the report",
}


def _resolve_repo(repo: str) -> Path:
    path = Path(repo).expanduser().resolve()
    if not (path / ".git").exists() and not (path / "HEAD").exists():
        # allow bare repos and worktrees
        if not path.name.endswith(".git"):
            console.print(f"[red]Not a git repository: {path}[/]")
            raise typer.Exit(1)
    return path


@app.command()
def analyze(
    repo: str = typer.Argument(..., help="Path to the repository to autopsy."),
    out: str = typer.Option(
        None, "--out", "-o",
        help="Where to write the report (default ~/Downloads/lastcommit-<repo>-report.md).",
    ),
    no_llm: bool = typer.Option(False, "--no-llm", help="Skip narrative synthesis; deterministic report only."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Produce the forensic report for an abandoned repository."""
    repo_path = _resolve_repo(repo)

    from .report.pipeline import generate_report

    with console.status("[dim]excavating…[/]") as status:
        result = generate_report(
            str(repo_path), no_llm=no_llm,
            progress=lambda s: status.update(f"[dim]{_STAGE_LABELS.get(s, s)}…[/]"),
        )

    if result.embedder:
        console.print(f"[green]✓[/] ingested: {result.commits} commits "
                      f"(embeddings: {result.embedder})")
    else:
        console.print(f"[green]✓[/] ingested: {result.commits} commits (keyword-only index)")
    console.print(f"[green]✓[/] forensic scan: {result.findings} findings")
    if not result.llm_used and not no_llm:
        console.print("[yellow]LLM host unreachable or key missing — deterministic mode.[/]")
    if result.llm_used:
        console.print("[green]✓[/] narrative synthesized and verified")

    if out:
        out_path = Path(out).expanduser()
    else:
        downloads = Path.home() / "Downloads"
        out_path = downloads / f"lastcommit-{result.repo_name}-report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.markdown, encoding="utf-8")

    console.print(Panel(f"Forensic report written to [bold]{out_path}[/]", border_style="green"))


@app.command()
def interview(
    repo: str = typer.Argument(..., help="Path to the repository whose maintainer you wish to interview."),
) -> None:
    """Interrogate the repository's former maintainer."""
    repo_path = _resolve_repo(repo)
    from .llm.client import LLMClient
    from .llm.config import load_config

    cfg = load_config()
    client = LLMClient(cfg.llm)
    if not client.available():
        console.print("[red]The interview requires a working model host "
                      "(Groq key in $GROQ_API_KEY, or configure ~/.config/lastcommit/config.toml).[/]")
        raise typer.Exit(1)

    from .index.build import ingest_repository

    with console.status("[dim]waking the maintainer…[/]"):
        snap, store, embedder = ingest_repository(str(repo_path), cfg.embed)
    from .interview.repl import run_interview

    run_interview(str(repo_path), snap, store, embedder, client)


@app.command()
def ingest(
    repo: str = typer.Argument(..., help="Path to the repository."),
) -> None:
    """Run ingestion + detectors and print a summary (debugging)."""
    repo_path = _resolve_repo(repo)
    from .analysis.signals import run_all_detectors
    from .index.build import ingest_repository
    from .llm.config import load_config

    cfg = load_config()
    snap, store, embedder = ingest_repository(str(repo_path), cfg.embed, verbose=True)
    findings = run_all_detectors(snap)
    for f in findings:
        console.print(f"[bold]{f.id}[/] ({f.category}/{f.severity}) {f.title}")
        for e in f.evidence[:3]:
            console.print(f"   · {e['ref']} — {e['label']}")
    console.print(f"\n{len(findings)} findings · {store.count()} chunks indexed")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8321, "--port", help="Port to serve on."),
) -> None:
    """Serve the web app (frontend + API) from one process."""
    import socket

    import uvicorn

    from .web.app import create_app

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        console.print(
            f"[red]Port {port} on {host} is already in use.[/] Is an older `lastcommit web` "
            f"still running? Stop it with `pkill -f 'lastcommit web'`, or serve elsewhere: "
            f"`lastcommit web --port {port + 1}`"
        )
        raise typer.Exit(1) from None
    finally:
        probe.close()

    console.print(Panel(f"The Last Commit — [bold]http://{host}:{port}[/]", border_style="green"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
