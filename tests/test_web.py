"""Web app tests: URL validation, rate limits, the job lifecycle, and
endpoint contracts — cloning is mocked to a local fixture repo, and the
LLM is forced off, so everything runs offline and fast."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fixtures import build_abandoned_repo  # noqa: E402

from lastcommit.web.github import parse_github_url  # noqa: E402
from lastcommit.web.ratelimit import RateLimiter, RateLimitExceeded  # noqa: E402


# ---------------------------------------------------------------- url parsing


def test_parse_github_url_accepts_repo_links():
    for url in (
        "https://github.com/owner/repo",
        "https://github.com/owner/repo/",
        "https://github.com/owner/repo.git",
        "https://www.github.com/Owner.Name/repo_name",
    ):
        parsed = parse_github_url(url)
        assert parsed.owner in ("owner", "Owner.Name")
        assert parsed.name in ("repo", "repo_name")
        assert parsed.clone_url.endswith(".git")


@pytest.mark.parametrize("url", [
    "http://github.com/owner/repo",          # not https
    "https://evil.com/owner/repo",           # not github
    "https://github.com/owner/repo/tree/main",  # subpath
    "https://github.com/owner",              # no repo
    "https://github.com/owner/repo?x=1",     # query
    "https://user:pass@github.com/owner/repo",  # credentials
    "https://github.com/../etc",             # traversal
    "not a url",
])
def test_parse_github_url_rejects(url):
    with pytest.raises(ValueError):
        parse_github_url(url)


# ---------------------------------------------------------------- rate limits


def test_rate_limiter_blocks_after_daily_cap(tmp_path):
    limiter = RateLimiter(tmp_path / "usage.db", per_ip_daily=2, global_daily=10)
    limiter.consume("1.2.3.4")
    limiter.consume("1.2.3.4")
    with pytest.raises(RateLimitExceeded):
        limiter.consume("1.2.3.4")
    # other IPs are unaffected, and the global budget keeps counting
    ip_left, global_left = limiter.consume("5.6.7.8")
    assert ip_left == 1 and global_left == 7


def test_rate_limiter_global_budget(tmp_path):
    limiter = RateLimiter(tmp_path / "usage.db", per_ip_daily=10, global_daily=2)
    limiter.consume("1.1.1.1")
    limiter.consume("2.2.2.2")
    with pytest.raises(RateLimitExceeded):
        limiter.consume("3.3.3.3")


def test_rate_limiter_persists_across_instances(tmp_path):
    limiter = RateLimiter(tmp_path / "usage.db", per_ip_daily=1, global_daily=10)
    limiter.consume("9.9.9.9")
    reborn = RateLimiter(tmp_path / "usage.db", per_ip_daily=1, global_daily=10)
    with pytest.raises(RateLimitExceeded):
        reborn.consume("9.9.9.9")


def test_rate_limiter_refund_restores_slot(tmp_path):
    limiter = RateLimiter(tmp_path / "usage.db", per_ip_daily=1, global_daily=1)
    limiter.consume("4.4.4.4")
    with pytest.raises(RateLimitExceeded):
        limiter.consume("4.4.4.4")
    limiter.refund("4.4.4.4")
    ip_left, global_left = limiter.consume("4.4.4.4")
    assert ip_left == 0 and global_left == 0  # both counters refunded


# ---------------------------------------------------------------- app flow


@pytest.fixture()
def web_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTCOMMIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LASTCOMMIT_EMBED_PROVIDER", "none")
    monkeypatch.setattr("lastcommit.llm.client.LLMClient.available", lambda self: False)
    fixture_repo = build_abandoned_repo(tmp_path / "fixture")
    import lastcommit.web.github as gh

    monkeypatch.setattr(
        gh, "clone_repo",
        lambda repo, dest, timeout=None: shutil.copytree(fixture_repo, dest, dirs_exist_ok=True),
    )
    from fastapi.testclient import TestClient

    from lastcommit.web.app import create_app

    return TestClient(create_app())


def _wait_for_done(client, job_id, timeout_s=60):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = client.get(f"/api/jobs/{job_id}").json()
        if data["status"] in ("done", "failed"):
            return data
        time.sleep(0.2)
    raise AssertionError("job never finished")


def test_full_report_flow(web_client):
    resp = web_client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["remaining_today"] == 4 and body["per_day_limit"] == 5
    assert body["repo"] == "ghost-siteswarm"

    final = _wait_for_done(web_client, body["job_id"])
    assert final["status"] == "done", final

    report = web_client.get(f"/api/jobs/{body['job_id']}/report")
    assert report.status_code == 200
    md = report.json()["markdown"]
    assert "## Summary" in md and "## Method" in md and "Case File" in md
    assert "siteswarm" in md  # named after the GitHub repo, not the temp clone dir

    download = web_client.get(f"/api/jobs/{body['job_id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/markdown")
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["content-disposition"].endswith('.md"')
    assert download.text == md


def test_per_ip_limit_enforced(web_client):
    for _ in range(5):
        resp = web_client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"})
        assert resp.status_code == 200
    sixth = web_client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"})
    assert sixth.status_code == 429
    assert "5 exhumations" in sixth.json()["detail"]


def test_bad_url_rejected(web_client):
    resp = web_client.post("/api/analyze", json={"url": "https://evil.com/owner/repo"})
    assert resp.status_code == 400
    assert resp.json()["detail"]


def test_unknown_job_404(web_client):
    assert web_client.get("/api/jobs/deadbeef").status_code == 404
    assert web_client.get("/api/jobs/deadbeef/report").status_code == 404


def test_frontend_served(web_client):
    index = web_client.get("/")
    assert index.status_code == 200
    assert "The Last Commit" in index.text
    for asset in ("/style.css", "/app.js", "/vendor/marked.min.js", "/vendor/purify.min.js"):
        assert web_client.get(asset).status_code == 200


def test_slim_toolbelt(tmp_path):
    from lastcommit.index.build import ingest_repository
    from lastcommit.index.store import ChunkStore  # noqa: F401
    from lastcommit.interview.tools import ToolBelt
    from lastcommit.llm.config import EmbedConfig

    fixture_repo = build_abandoned_repo(tmp_path / "fixture")
    snap, store, _ = ingest_repository(str(fixture_repo), EmbedConfig(provider="none"))
    slim = ToolBelt(str(fixture_repo), store, None, slim=True)
    schemas = slim.schemas()
    assert len(schemas) == 5
    assert slim.max_output == 1200
    full = ToolBelt(str(fixture_repo), store, None)
    assert len(full.schemas()) == 8


def test_web_chat_flow(tmp_path, monkeypatch):
    """Report → chat: the maintainer answers with receipts, using a tool round."""
    monkeypatch.setenv("LASTCOMMIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LASTCOMMIT_EMBED_PROVIDER", "none")
    from lastcommit.llm.client import LLMClient

    monkeypatch.setattr(LLMClient, "available", lambda self: True)
    fixture_repo = build_abandoned_repo(tmp_path / "fixture")
    import lastcommit.web.github as gh

    monkeypatch.setattr(
        gh, "clone_repo",
        lambda repo, dest, timeout=None: shutil.copytree(fixture_repo, dest, dirs_exist_ok=True),
    )

    state = {"rounds": 0}

    def fake_chat(self, messages, tools=None, temperature=0.2, max_tokens=2048):
        if tools and state["rounds"] == 0:
            state["rounds"] += 1
            return {"message": {
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "search_repo",
                                             "arguments": '{"query": "cache"}'}}],
            }}
        return {"message": {"role": "assistant", "content":
                "We ripped the cache out after it doubled latency. **Receipts:**\n"
                "- b1b3f97 final known commit"}}

    monkeypatch.setattr(LLMClient, "chat", fake_chat)

    from fastapi.testclient import TestClient

    from lastcommit.web.app import create_app

    client = TestClient(create_app())
    job = client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"}).json()
    final = _wait_for_done(client, job["job_id"])
    assert final["status"] == "done"

    resp = client.post(f"/api/jobs/{job['job_id']}/chat",
                       json={"question": "Why did the cache layer disappear?"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Receipts:" in body["answer"] and "cache" in body["answer"].lower()
    assert body["per_day_limit"] == 20
    assert state["rounds"] == 1  # the tool actually ran

    transcript = client.get(f"/api/jobs/{job['job_id']}/chat").json()["history"]
    assert [m["role"] for m in transcript] == ["user", "assistant"]

    # chatting before/without a done report fails cleanly
    assert client.post("/api/jobs/nope/chat", json={"question": "hi"}).status_code == 404


def test_web_chat_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTCOMMIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LASTCOMMIT_EMBED_PROVIDER", "none")
    monkeypatch.setenv("LASTCOMMIT_DAILY_CHAT_PER_IP", "2")
    from lastcommit.llm.client import LLMClient

    monkeypatch.setattr(LLMClient, "available", lambda self: True)
    monkeypatch.setattr(
        LLMClient, "chat",
        lambda self, messages, tools=None, temperature=0.2, max_tokens=2048:
        {"message": {"role": "assistant", "content": "hm. **Receipts:**\n- none — the record is silent"}},
    )
    fixture_repo = build_abandoned_repo(tmp_path / "fixture")
    import lastcommit.web.github as gh

    monkeypatch.setattr(
        gh, "clone_repo",
        lambda repo, dest, timeout=None: shutil.copytree(fixture_repo, dest, dirs_exist_ok=True),
    )

    from fastapi.testclient import TestClient

    from lastcommit.web.app import create_app

    client = TestClient(create_app())
    job = client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"}).json()
    _wait_for_done(client, job["job_id"])
    url = f"/api/jobs/{job['job_id']}/chat"
    assert client.post(url, json={"question": "a"}).status_code == 200
    assert client.post(url, json={"question": "b"}).status_code == 200
    third = client.post(url, json={"question": "c"})
    assert third.status_code == 429
    assert "whispers" in third.json()["detail"] or "2" in third.json()["detail"]


def test_web_chat_needs_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("LASTCOMMIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LASTCOMMIT_EMBED_PROVIDER", "none")
    from lastcommit.llm.client import LLMClient

    monkeypatch.setattr(LLMClient, "available", lambda self: False)
    fixture_repo = build_abandoned_repo(tmp_path / "fixture")
    import lastcommit.web.github as gh

    monkeypatch.setattr(
        gh, "clone_repo",
        lambda repo, dest, timeout=None: shutil.copytree(fixture_repo, dest, dirs_exist_ok=True),
    )

    from fastapi.testclient import TestClient

    from lastcommit.web.app import create_app

    client = TestClient(create_app())
    job = client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"}).json()
    _wait_for_done(client, job["job_id"])
    resp = client.post(f"/api/jobs/{job['job_id']}/chat", json={"question": "hello?"})
    assert resp.status_code == 503
    assert "cannot be woken" in resp.json()["detail"]


def test_failed_job_surfaces_error_and_refunds_quota(tmp_path, monkeypatch):
    """A failed analysis must not consume the daily budget."""
    monkeypatch.setenv("LASTCOMMIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LASTCOMMIT_EMBED_PROVIDER", "none")
    monkeypatch.setattr("lastcommit.llm.client.LLMClient.available", lambda self: False)
    fixture_repo = build_abandoned_repo(tmp_path / "fixture")
    import lastcommit.web.github as gh

    monkeypatch.setattr(
        gh, "clone_repo",
        lambda repo, dest, timeout=None: shutil.copytree(fixture_repo, dest, dirs_exist_ok=True),
    )

    from fastapi.testclient import TestClient

    from lastcommit.web.app import create_app

    client = TestClient(create_app())

    ok = client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"})
    assert ok.status_code == 200
    assert ok.json()["remaining_today"] == 4

    def boom(repo, dest, timeout=None):
        raise RuntimeError("the crypt collapsed")

    monkeypatch.setattr(gh, "clone_repo", boom)
    failed = client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"})
    final = _wait_for_done(client, failed.json()["job_id"])
    assert final["status"] == "failed"
    assert "crypt collapsed" in final["error"]
    assert client.get(f"/api/jobs/{failed.json()['job_id']}/report").status_code == 409

    # the failed job refunded its slot: the next POST sees 3 remaining
    # (5 − 1 success − 1 now in flight), not 2
    monkeypatch.setattr(
        gh, "clone_repo",
        lambda repo, dest, timeout=None: shutil.copytree(fixture_repo, dest, dirs_exist_ok=True),
    )
    third = client.post("/api/analyze", json={"url": "https://github.com/ghost/siteswarm"})
    assert third.status_code == 200
    assert third.json()["remaining_today"] == 3
