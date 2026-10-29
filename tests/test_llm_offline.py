"""LLM-path tests against a mock OpenAI-compatible server — no network,
no API key.  Validates narrative synthesis, citation verification, roadmap
parsing, and the interview tool loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fixtures import build_abandoned_repo  # noqa: E402

from lastcommit.analysis.narrative import (  # noqa: E402
    synthesize_narrative,
    synthesize_roadmap,
)
from lastcommit.analysis.signals import run_all_detectors  # noqa: E402
from lastcommit.analysis.verify import verify_narrative  # noqa: E402
from lastcommit.index.build import ingest_repository  # noqa: E402
from lastcommit.interview.persona import ask  # noqa: E402
from lastcommit.interview.tools import ToolBelt  # noqa: E402
from lastcommit.llm.client import LLMClient  # noqa: E402
from lastcommit.llm.config import EmbedConfig, LLMConfig  # noqa: E402


def _completion(message: dict) -> dict:
    return {
        "id": "chatcmpl-mock", "object": "chat.completion", "created": 0,
        "model": "mock", "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
    }


def mock_client(handler) -> LLMClient:
    cfg = LLMConfig(base_url="http://mock/v1", model="mock-model", api_key="test-key")
    client = LLMClient(cfg)
    client._http = httpx.Client(transport=httpx.MockTransport(handler), base_url=cfg.base_url)
    return client


@pytest.fixture(scope="module")
def forensic(tmp_path_factory):
    repo = build_abandoned_repo(tmp_path_factory.mktemp("llmrepo"))
    snap, store, _ = ingest_repository(str(repo), EmbedConfig(provider="none"))
    findings = run_all_detectors(snap)
    return snap, store, findings


def test_client_available_and_chat(forensic):
    _, _, _ = forensic
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "mock-model"}]})
        return httpx.Response(200, json=_completion({"role": "assistant", "content": "hello"}))

    client = mock_client(handler)
    assert client.available()
    assert client.chat_text([{"role": "user", "content": "hi"}]) == "hello"
    assert calls[1].url.path == "/v1/chat/completions"


def test_narrative_cites_and_verifier_strips_inventions(forensic):
    snap, _, findings = forensic
    good_sha = snap.commits[0].short_sha

    def handler(request: httpx.Request) -> httpx.Response:
        text = (f"The project died at [F-001] and [F-002]. The revert {good_sha} shows regret. "
                "A wild deadbeefcafe123 appears from nowhere.")
        return httpx.Response(200, json=_completion({"role": "assistant", "content": text}))

    client = mock_client(handler)
    raw = synthesize_narrative(snap, findings, client)
    assert raw  # sections written
    verified = verify_narrative(raw, findings, snap)
    joined = "\n".join(verified.values())
    assert "[F-001]" in joined and "[F-002]" in joined
    assert good_sha in joined
    assert "deadbeefcafe123" not in joined


def test_roadmap_json_parsed_and_scrubbed(forensic):
    snap, _, findings = forensic
    payload = json.dumps([
        {"title": "Rescue the parquet export", "why": "Branch strands work [F-005]",
         "first_step": "cherry-pick tip deadbeef99999 onto main", "risk": "API drift", "cites": ["F-005"]},
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion({"role": "assistant", "content": payload}))

    client = mock_client(handler)
    roadmap = synthesize_roadmap(snap, findings, client)
    assert "Rescue the parquet export" in roadmap
    assert "[F-005]" in roadmap
    assert "deadbeef99999" not in roadmap


def test_interview_tool_loop_ends_with_receipts(forensic):
    snap, store, _ = forensic
    belt = ToolBelt(str(snap.path), store, None)
    state = {"rounds": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        last_role = body["messages"][-1]["role"]
        if "tools" in body and last_role == "user" and state["rounds"] == 0:
            state["rounds"] += 1
            return httpx.Response(200, json=_completion({
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "search_history",
                                             "arguments": json.dumps({"query": "cache"})}}],
            }))
        return httpx.Response(200, json=_completion({
            "role": "assistant",
            "content": "We ripped the cache out after it doubled latency. **Receipts:**\n- none — the record is silent",
        }))

    client = mock_client(handler)
    answer, history = ask(belt, client, snap, [], "Why did the cache layer disappear?")
    assert "cache" in answer.lower()
    assert "**Receipts:**" in answer
    assert len(history) == 2  # question + answer
    # the tool actually ran: the transcript-level proof is that round 2 happened
    assert state["rounds"] == 1


def test_tool_belt_executes_real_git(forensic):
    snap, store, _ = forensic
    belt = ToolBelt(str(snap.path), store, None)
    revert = next(c for c in snap.commits if c.summary.lower().startswith("revert"))
    out = belt.execute("show_commit", json.dumps({"sha": revert.short_sha}))
    assert revert.summary in out
    out = belt.execute("file_at_rev", json.dumps({"path": "src/scraper/engine.py", "rev": "HEAD"}))
    assert "class Engine" in out
    out = belt.execute("list_branches", "{}")
    assert "feature/parquet-export" in out
