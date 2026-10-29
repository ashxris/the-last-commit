"""The Last Commit — command line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from .analysis.signals import run_all_detectors
from .index.build import ingest_repository
from .llm.client import LLMClient
from .llm.config import load_config

app = typer.Typer(
    name="lastcommit",
    help="An AI archaeologist for abandoned repositories.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _resolve_repo(repo: str) -> Path:
    path = Path(repo).expanduser().resolve()
    if not (path / ".git").exists() and not (path / "HEAD").exists():
        # allow bare repos and worktrees
        if not path.name.endswith(".git"):
            console.print(f"[red]Not a git repository: {path}[/]")
            raise typer.Exit(1)
    return path


def _make_client(no_llm: bool):
    cfg = load_config()
    client = LLMClient(cfg.llm)
    llm_ok = False
    if not no_llm:
        with console.status("[dim]reaching for the model host…[/]"):
            llm_ok = client.available()
        if not llm_ok:
            console.print("[yellow]LLM host unreachable or key missing — deterministic mode.[/]")
    return cfg, client, llm_ok


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
    cfg, client, llm_ok = _make_client(no_llm)

    with console.status("[dim]excavating the repository…[/]"):
        snap, store, embedder = ingest_repository(str(repo_path), cfg.embed, verbose=verbose)
    console.print(f"[green]✓[/] ingested: {len(snap.commits)} commits, {len(snap.symbols)} symbols, "
                  f"{len(snap.todos)} markers, {store.count()} chunks "
                  f"({'embeddings: ' + embedder.name if embedder else 'keyword-only index'})")

    findings = run_all_detectors(snap)
    console.print(f"[green]✓[/] forensic scan: {len(findings)} findings")

    narratives: dict[str, str] = {}
    roadmap = ""
    llm_used = False
    if llm_ok and findings:
        from .analysis import narrative as nar

        with console.status("[dim]the model is reading the evidence…[/]"):
            narratives = nar.synthesize_narrative(snap, findings, client)
            narratives = nar.verify_narrative(narratives, findings, snap)
            roadmap = nar.synthesize_roadmap(snap, findings, client)
        llm_used = bool(narratives or roadmap)
        console.print(f"[green]✓[/] narrative synthesized{' and verified' if llm_used else ''}")

    from .report.renderer import render_report

    report = render_report(snap, findings, narratives, roadmap, llm_used)
    if out:
        out_path = Path(out).expanduser()
    else:
        downloads = Path.home() / "Downloads"
        out_path = downloads / f"lastcommit-{snap.name}-report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    console.print(Panel(f"Forensic report written to [bold]{out_path}[/]", border_style="green"))


@app.command()
def interview(
    repo: str = typer.Argument(..., help="Path to the repository whose maintainer you wish to interview."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Pointless, but honest: the interview needs a model."),
) -> None:
    """Interrogate the repository's former maintainer."""
    repo_path = _resolve_repo(repo)
    cfg, client, llm_ok = _make_client(no_llm)
    if not llm_ok:
        console.print("[red]The interview requires a working model host "
                      "(Groq key in $GROQ_API_KEY, or configure ~/.config/lastcommit/config.toml).[/]")
        raise typer.Exit(1)

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
    cfg = load_config()
    snap, store, embedder = ingest_repository(str(repo_path), cfg.embed, verbose=True)
    findings = run_all_detectors(snap)
    for f in findings:
        console.print(f"[bold]{f.id}[/] ({f.category}/{f.severity}) {f.title}")
        for e in f.evidence[:3]:
            console.print(f"   · {e['ref']} — {e['label']}")
    console.print(f"\n{len(findings)} findings · {store.count()} chunks indexed")


if __name__ == "__main__":
    app()
