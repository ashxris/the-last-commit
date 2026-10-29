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

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "repo": self.repo_slug,
        }


class JobManager:
    def __init__(self, max_workers: int = 3, max_jobs: int = 200):
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="exhume")
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._max_jobs = max_jobs

    def create(self, url: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], url=url)
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
        except Exception as exc:  # noqa: BLE001 — any failure must surface as a failed job
            with self._lock:
                job = self._jobs.get(job_id)
                if job:
                    job.status = "failed"
                    job.error = str(exc)
        finally:
            shutil.rmtree(dest, ignore_errors=True)
