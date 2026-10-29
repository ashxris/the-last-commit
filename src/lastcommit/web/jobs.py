"""Background analysis jobs: submit a GitHub URL, poll for status, fetch
the finished markdown.  In-memory job table + a small thread pool — the
right shape for ~100 users/day."""

from __future__ import annotations

import shutil
import tempfile
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..report.pipeline import generate_report
from . import github as gh


@dataclass
class Job:
    id: str
    url: str
    status: str = "queued"  # queued | cloning | ingest | scan | narrate | render | done | failed
    stage: str = "Waiting in the crypt…"
    error: str | None = None
    repo_slug: str = ""
    markdown: str | None = None
    created: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ip: str = "unknown"
    dest_dir: str | None = None   # kept after success so the interview can consult git
    brief: dict = field(default_factory=dict)
    chat_history: list = field(default_factory=list)

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "repo": self.repo_slug,
        }


MAX_KEPT_CLONES = 12  # done jobs whose clone dirs stay alive for the interview


class JobManager:
    def __init__(self, max_workers: int = 3, max_jobs: int = 200,
                 on_failure=None):
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="exhume")
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._kept_dirs: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()
        self._max_jobs = max_jobs
        self._on_failure = on_failure  # fn(ip) -> None, refunds the daily slot

    def create(self, url: str, ip: str = "unknown") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], url=url)
        job.ip = ip
        try:
            repo = gh.parse_github_url(url)
            job.repo_slug = repo.slug
        except ValueError as exc:
            job.status = "failed"
            job.error = str(exc)
            self._register(job)
            return job
        self._register(job)
        self.pool.submit(self._run, job.id, repo)
        return job

    def _register(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > self._max_jobs:
                oldest_id, _ = next(iter(self._jobs.items()))
                if oldest_id == job.id:
                    break
                del self._jobs[oldest_id]
                stale = self._kept_dirs.pop(oldest_id, None)
                if stale:
                    shutil.rmtree(stale, ignore_errors=True)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _set_stage(self, job_id: str, status: str, stage: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = status
                job.stage = stage

    def _run(self, job_id: str, repo: gh.GitHubRepo) -> None:
        dest = tempfile.mkdtemp(prefix="lastcommit-")
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.dest_dir = dest
        try:
            self._set_stage(job_id, "cloning", "Digging up the repository…")
            gh.clone_repo(repo, dest)

            def progress(stage_key: str) -> None:
                stages = {
                    "llm-check": ("llm-check", "Consulting the spirits…"),
                    "ingest": ("ingest", "Reading the bones…"),
                    "scan": ("scan", "Running the forensic scan…"),
                    "narrate": ("narrate", "Summoning the narrative…"),
                    "render": ("render", "Inscribing the report…"),
                }
                status, stage = stages.get(stage_key, (stage_key, stage_key))
                self._set_stage(job_id, status, stage)

            result = generate_report(dest, progress=progress, repo_name=repo.name)
            with self._lock:
                job = self._jobs[job_id]
                job.status = "done"
                job.stage = "The report is ready."
                job.markdown = result.markdown
                # keep the GitHub slug for filenames; result.repo_name is only display
                job.brief = result.brief
                self._kept_dirs[job_id] = dest
                while len(self._kept_dirs) > MAX_KEPT_CLONES:
                    _, stale = self._kept_dirs.popitem(last=False)
                    if stale != dest:
                        shutil.rmtree(stale, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001 — any failure must surface as a failed job
            with self._lock:
                job = self._jobs.get(job_id)
                if job:
                    job.status = "failed"
                    job.error = str(exc)
            if self._on_failure:
                try:
                    self._on_failure(job.ip)
                except Exception:  # noqa: BLE001 — refund must never crash the worker
                    pass
        finally:
            # done jobs keep their clone dir (the interview reads git from it);
            # everything else is cleaned up immediately
            with self._lock:
                succeeded = job_id in self._kept_dirs
            if not succeeded:
                shutil.rmtree(dest, ignore_errors=True)
