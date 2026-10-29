"""End-to-end and unit tests over the synthetic abandoned repo."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fixtures import build_abandoned_repo  # noqa: E402

from lastcommit.analysis.signals import monthly_cadence, run_all_detectors  # noqa: E402
from lastcommit.index.embeddings import get_embedder  # noqa: E402
from lastcommit.index.search import hybrid_search  # noqa: E402
from lastcommit.ingest.code_parser import extract_python_symbols, extract_todos  # noqa: E402
from lastcommit.ingest.git_history import extract_git_history  # noqa: E402
from lastcommit.llm.config import EmbedConfig  # noqa: E402


@pytest.fixture(scope="session")
def repo(tmp_path_factory) -> Path:
    return build_abandoned_repo(tmp_path_factory.mktemp("abandoned"))


@pytest.fixture(scope="session")
def snapshot(repo):
    from lastcommit.ingest.pipeline import build_snapshot

    return build_snapshot(str(repo))


# ---------------------------------------------------------------- git history


def test_commits_extracted(snapshot):
    assert len(snapshot.commits) == 9  # 8 on main + 1 on the dangling branch
    assert snapshot.default_branch == "main"
    names = {c.summary for c in snapshot.commits}
    assert "Initial crawl loop and engine skeleton" in names
    assert "Fix broken export after the pools refactor" in names


def test_authors_and_dropout_order(snapshot):
    assert set(snapshot.authors) == {"Ada Wrench", "Bob Solder"}
    ordered = sorted(snapshot.authors.values(), key=lambda a: a.last_commit)
    assert ordered[0].name == "Ada Wrench"  # she left first
    assert ordered[-1].name == "Bob Solder"


def test_dangling_branch_detected(snapshot):
    dangling = [b for b in snapshot.branches if not b.merged]
    assert [b.name for b in dangling] == ["feature/parquet-export"]
    assert "parquet export draft" in dangling[0].tip_summary


def test_head_sha(snapshot):
    assert len(snapshot.head_sha) == 40


# ---------------------------------------------------------------- code parser


def test_python_symbols_and_stubs(repo):
    src = (repo / "src/scraper/engine.py").read_bytes()
    symbols, err = extract_python_symbols("src/scraper/engine.py", src)
    assert err is None
    names = {s.name: s for s in symbols}
    assert {"Engine", "crawl", "parse_rate_limit", "export_results"} <= set(names)
    assert names["Engine"].kind == "class"
    assert names["crawl"].kind == "method"
    assert names["export_results"].is_stub
    assert "NotImplementedError" in names["export_results"].stub_reason
    assert not names["crawl"].is_stub


def test_todo_extraction(repo):
    text = (repo / "src/scraper/engine.py").read_text()
    todos = extract_todos("src/scraper/engine.py", text)
    kinds = {t.kind for t in todos}
    assert {"TODO", "FIXME"} <= kinds


def test_skip_markers(repo):
    text = (repo / "tests/test_engine.py").read_text()
    todos = extract_todos("tests/test_engine.py", text)
    assert any(t.kind == "SKIP" for t in todos)


# ---------------------------------------------------------------- detectors


@pytest.fixture(scope="session")
def findings(snapshot):
    return run_all_detectors(snapshot)


def test_finding_ids_assigned(findings):
    assert findings
    assert [f.id for f in findings] == [f"F-{i:03d}" for i in range(1, len(findings) + 1)]


def test_unfinished_findings(findings):
    unfinished = "\n".join(f.title for f in findings if f.category == "unfinished")
    assert "stub symbol" in unfinished
    assert "TODO/FIXME" in unfinished
    assert "never merged" in unfinished
    assert "CHANGELOG" in unfinished  # the Unreleased section


def test_regression_findings(findings):
    regressions = "\n".join(f.title for f in findings if f.category == "regression")
    assert "deliberately undone" in regressions  # the revert
    assert "regret" in regressions
    assert "skipped/disabled" in regressions


def test_decision_findings(findings):
    decisions = "\n".join(f.title for f in findings if f.category == "decision")
    assert "no documentation was touched" in decisions


def test_cadence(snapshot):
    cadence = monthly_cadence(snapshot)
    assert cadence["2023-01"] == 2
    assert cadence["2023-04"] == 2  # final tweaks + dangling branch tip


# ---------------------------------------------------------------- index & search


@pytest.fixture(scope="session")
def index(repo):
    from lastcommit.index.build import ingest_repository

    snap, store, _ = ingest_repository(str(repo), EmbedConfig(provider="none"))
    return snap, store


def test_store_populated(index):
    _, store = index
    assert store.count() >= 25
    kinds = {c.kind for _, c in store.all_chunks()}
    assert {"commit", "code", "doc", "todo"} <= kinds


def test_fts_search(index):
    _, store = index
    hits = store.fts_search("experimental cache layer", k=5)
    assert hits
    top = store.get_chunk(hits[0][0])
    assert "cache" in top.title.lower()


def test_hybrid_search_keyword_only(index):
    _, store = index
    hits = hybrid_search(store, None, "oauth", k=5)
    assert hits
    assert any("oauth" in h.chunk.title.lower() or "oauth" in h.chunk.content.lower() for h in hits)


def test_search_todo_kind(index):
    _, store = index
    hits = hybrid_search(store, None, "parquet", kinds=["todo"], k=5)
    assert all(h.chunk.kind == "todo" for h in hits)


def test_embedder_none_provider():
    assert get_embedder(EmbedConfig(provider="none")) is None


# ---------------------------------------------------------------- verify


def test_verifier_strips_invented_sha(snapshot, findings):
    from lastcommit.analysis.verify import verify_text

    good = f"Work stalled [F-001] around {snapshot.commits[-1].short_sha}."
    bad = "Work stalled [F-999] around deadbeefdeadbeef."
    clean_good, _ = verify_text(good, findings, snapshot)
    clean_bad, removals = verify_text(bad, findings, snapshot)
    assert "[F-001]" in clean_good
    assert "[F-999]" not in clean_bad and "deadbeef" not in clean_bad
    assert removals


# ---------------------------------------------------------------- e2e report


def test_analyze_e2e_deterministic(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("LASTCOMMIT_EMBED_PROVIDER", "none")
    from typer.testing import CliRunner

    from lastcommit.cli import app

    out = tmp_path / "report.md"
    result = CliRunner().invoke(app, ["analyze", str(repo), "--out", str(out), "--no-llm"])
    assert result.exit_code == 0, result.output
    text = out.read_text()
    for heading in ("Case File", "Timeline of Decline", "The Autopsy",
                    "Anomalies", "Revival Roadmap", "Method"):
        assert heading in text
    assert "Ada Wrench" in text
    assert "feature/parquet-export" in text
    assert "F-0" in text  # finding citations present
