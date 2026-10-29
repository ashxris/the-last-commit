"""FastAPI application: static spooky frontend + JSON API.

Secrets stay here.  The frontend talks only to /api/* endpoints and never
sees an API key, the git client, or the filesystem.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..llm.client import LLMClient
from ..llm.config import load_config
from .chat import chat_turn
from .github import parse_github_url
from .jobs import JobManager
from .ratelimit import RateLimiter, RateLimitExceeded

STATIC_DIR = Path(__file__).parent / "static"
FWD_FOR_RE = re.compile(r"^\s*([0-9a-fA-F:.]+)")


class AnalyzeRequest(BaseModel):
    """Module level on purpose: with `from __future__ import annotations`,
    FastAPI cannot resolve models defined inside a closure."""

    url: str


class ChatRequest(BaseModel):
    question: str


def _client_ip(request: Request) -> str:
    if os.environ.get("LASTCOMMIT_TRUST_PROXY", "") == "1":
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            m = FWD_FOR_RE.match(fwd)
            if m:
                return m.group(1)
    return request.client.host if request.client else "unknown"


def create_app() -> FastAPI:
    data_dir = Path(os.environ.get("LASTCOMMIT_DATA_DIR", Path.home() / ".local" / "state" / "lastcommit"))
    limiter = RateLimiter(
        db_path=data_dir / "usage.db",
        per_ip_daily=int(os.environ.get("LASTCOMMIT_DAILY_PER_IP", "5")),
        global_daily=int(os.environ.get("LASTCOMMIT_DAILY_GLOBAL", "100")),
    )
    chat_limiter = RateLimiter(
        db_path=data_dir / "usage.db",
        per_ip_daily=int(os.environ.get("LASTCOMMIT_DAILY_CHAT_PER_IP", "20")),
        global_daily=int(os.environ.get("LASTCOMMIT_DAILY_CHAT_GLOBAL", "100")),
        prefix="chat:",
    )
    jobs = JobManager(
        max_workers=int(os.environ.get("LASTCOMMIT_WORKERS", "3")),
        on_failure=limiter.refund,
    )
    llm = LLMClient(load_config().llm)

    # The web app never interviews (no embeddings needed) — keep it light.
    os.environ.setdefault("LASTCOMMIT_EMBED_PROVIDER", "none")

    app = FastAPI(title="The Last Commit", docs_url=None, redoc_url=None)

    @app.post("/api/analyze")
    def start_analysis(body: AnalyzeRequest, request: Request):
        try:
            parse_github_url(body.url)  # fail fast on bad input
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ip = _client_ip(request)
        try:
            ip_left, global_left = limiter.consume(ip)
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.message) from exc
        job = jobs.create(body.url, ip=ip)
        return {
            "job_id": job.id,
            "repo": job.repo_slug,
            "remaining_today": ip_left,
            "per_day_limit": limiter.per_ip_daily,
            "global_left_today": global_left,
        }

    def _job_or_404(job_id: str):
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such exhumation.")
        return job

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str):
        return _job_or_404(job_id).public()

    @app.get("/api/jobs/{job_id}/report")
    def job_report(job_id: str):
        job = _job_or_404(job_id)
        if job.status != "done" or job.markdown is None:
            raise HTTPException(status_code=409, detail="Report not ready.")
        return {"markdown": job.markdown, "repo": job.repo_slug}

    @app.get("/api/jobs/{job_id}/download")
    def job_download(job_id: str):
        job = _job_or_404(job_id)
        if job.status != "done" or job.markdown is None:
            raise HTTPException(status_code=409, detail="Report not ready.")
        filename = f"lastcommit-{job.repo_slug or 'report'}-report.md"
        return PlainTextResponse(
            job.markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/jobs/{job_id}/chat")
    def job_chat(job_id: str, body: ChatRequest, request: Request):
        job = _job_or_404(job_id)
        if job.status != "done" or job.markdown is None or not job.dest_dir:
            raise HTTPException(status_code=409, detail="Exhume the report first — the dead must be "
                                                        "present before they can speak.")
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="Whisper something first.")
        if not llm.available():
            raise HTTPException(status_code=503, detail="The maintainer cannot be woken — no LLM "
                                                        "host or API key configured.")
        ip = _client_ip(request)
        try:
            chat_left, _ = chat_limiter.consume(ip)
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=exc.message) from exc

        from ..index.store import ChunkStore
        from ..interview.tools import ToolBelt

        belt = ToolBelt(job.dest_dir, ChunkStore(job.dest_dir), None, slim=True)
        answer, new_history = chat_turn(belt, llm, job.brief, job.markdown, job.chat_history, question)
        job.chat_history = new_history[-20:]
        return {
            "answer": answer,
            "remaining_today": chat_left,
            "per_day_limit": chat_limiter.per_ip_daily,
        }

    @app.get("/api/jobs/{job_id}/chat")
    def job_chat_history(job_id: str):
        job = _job_or_404(job_id)
        if job.status != "done":
            raise HTTPException(status_code=409, detail="Report not ready.")
        return {"history": job.chat_history}

    # API routes are registered first; the static frontend catches the rest.
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
