"""Build a small synthetic *abandoned* repository with known forensics:
two authors (one leaves early), a dangling branch, a revert, WIP commits,
stubs, TODOs, a skipped test, and an undocumented refactor."""

from __future__ import annotations

import subprocess
from pathlib import Path

# (date, author, message, files: {path: content} | None for deletions)
ENGINE = '''"""The scraping engine."""


class Engine:
    """Drives scraping sessions."""

    def __init__(self, depth: int = 3):
        self.depth = depth
        self.visited: set[str] = set()

    def crawl(self, url: str) -> list[str]:
        """Crawl a page and return discovered links."""
        # TODO: respect robots.txt (ticket #42)
        results = [url]
        if self.depth <= 0:
            return results
        self.visited.add(url)
        return results

    def parse_rate_limit(self, headers: dict) -> float:
        """Figure out the retry-after from headers."""
        # FIXME: this breaks on Cloudflare responses
        if "Retry-After" in headers:
            return float(headers["Retry-After"])
        return 1.0

    def export_results(self, fmt: str):
        """Export to csv/json/parquet."""
        raise NotImplementedError("planned for 0.4")
'''

ORPHAN = '''"""Legacy helpers from the 0.1 era."""


def html_table_to_csv(html: str) -> str:
    """Convert an HTML table to CSV. Nobody imports this anymore."""
    return html.replace("<td>", ",").replace("</td>", "")
'''

CACHE = '''"""Experimental cache layer (reverted after latency regression)."""


class ResultCache:
    def get(self, key: str):
        pass  # TODO: wire into redis?

    def put(self, key: str, value) -> None:
        pass
'''

TEST_ENGINE = '''import pytest
from scraper.engine import Engine


def test_crawl_returns_seed():
    e = Engine(depth=1)
    assert e.crawl("http://x") == ["http://x"]


@pytest.mark.skip(reason="flaky on CI since March, disable until #57 lands")
def test_rate_limit_cloudflare():
    e = Engine()
    assert e.parse_rate_limit({"CF-Retry-After": "3"}) == 3.0
'''

README = '''# siteswarm

> A parallel web scraper for building datasets fast.

siteswarm crawls thousands of URLs concurrently, respects rate limits,
and exports clean datasets.

## Usage

    python -m siteswarm --config swarm.toml urls.txt

## Status

EXPERIMENTAL. Expect API changes.
'''

CHANGELOG = '''# Changelog

## Unreleased
- robots.txt support (#42)
- parquet export
- oauth token refresh for authenticated crawls

## 0.2.0 — 2023-02-14
- concurrent crawler pools
- rate limit parsing

## 0.1.0 — 2023-01-09
- first working crawl
- csv export
'''

REQS = "httpx>=0.24\nbeautifulsoup4>=4.11\n"

BIG_MODULE = "\n".join(f"# filler line {i} of the pools refactor" for i in range(380))


def _git(cwd: Path, *args: str, date: str | None = None, author: str = "ada"):
    env = {
        "GIT_AUTHOR_NAME": "Ada Wrench" if author == "ada" else "Bob Solder",
        "GIT_AUTHOR_EMAIL": "ada@example.com" if author == "ada" else "bob@example.com",
        "GIT_COMMITTER_NAME": "Ada Wrench" if author == "ada" else "Bob Solder",
        "GIT_COMMITTER_EMAIL": "ada@example.com" if author == "ada" else "bob@example.com",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


def build_abandoned_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Ada Wrench")
    _git(path, "config", "user.email", "ada@example.com")

    def write(files: dict[str, str]) -> None:
        for rel, content in files.items():
            p = path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

    # January 2023: birth
    write({"README.md": README, "requirements.txt": REQS,
           "src/scraper/__init__.py": "", "src/scraper/engine.py": ENGINE})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "Initial crawl loop and engine skeleton",
         date="2023-01-09T10:00:00+00:00", author="ada")

    write({"CHANGELOG.md": CHANGELOG, "src/scraper/orphan.py": ORPHAN})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "Add changelog; legacy table exporter",
         date="2023-01-16T11:00:00+00:00", author="ada")

    # February: bob joins, big refactor with no doc updates
    write({"src/scraper/pools.py": BIG_MODULE, "src/scraper/engine.py": ENGINE + "\n# refactored for pools\n"})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "Refactor crawler into worker pools across modules",
         date="2023-02-03T09:00:00+00:00", author="bob")

    # experimental cache, then revert
    write({"src/scraper/cache.py": CACHE})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "Add experimental cache layer",
         date="2023-02-10T15:00:00+00:00", author="bob")
    _git(path, "rm", "-q", "src/scraper/cache.py")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", 'Revert "Add experimental cache layer" — latency doubled, sorry',
         date="2023-02-17T16:00:00+00:00", author="bob")

    # tests with a skip
    write({"tests/test_engine.py": TEST_ENGINE, "tests/__init__.py": ""})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "Tests for engine; skip flaky cloudflare case",
         date="2023-02-24T10:00:00+00:00", author="ada")

    # March: wip commit, then ada leaves
    write({"src/scraper/oauth.py": "# TODO: oauth token refresh (#43)\n\ndef refresh(token: str):\n    ...\n"})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "wip oauth",
         date="2023-03-06T12:00:00+00:00", author="ada")

    # April: bob alone, one last commit, then silence
    write({"src/scraper/engine.py": ENGINE + "\n\n# final tweaks before the freeze\n"})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "Fix broken export after the pools refactor",
         date="2023-04-12T18:00:00+00:00", author="bob")

    # dangling branch: work stranded
    _git(path, "checkout", "-q", "-b", "feature/parquet-export")
    write({"src/scraper/parquet.py": "# TODO: parquet export (#44)\n\ndef export_parquet(rows):\n    ...\n"})
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "wip: parquet export draft",
         date="2023-04-15T09:00:00+00:00", author="bob")
    _git(path, "checkout", "-q", "main")

    return path
