"""GitHub URL validation and cloning.  The only network egress the web
app performs on a user's behalf is a `git clone` of a github.com URL."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

ALLOWED_HOSTS = {"github.com", "www.github.com"}
REPO_PATH_RE = re.compile(r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")

DEFAULT_CLONE_TIMEOUT = 300  # seconds


@dataclass
class GitHubRepo:
    owner: str
    name: str
    clone_url: str

    @property
    def slug(self) -> str:
        return f"{self.owner}-{self.name}"


def parse_github_url(url: str) -> GitHubRepo:
    """Accept https://github.com/owner/repo[.git][/], nothing else."""
    if not url or not isinstance(url, str):
        raise ValueError("Empty URL")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https",):
        raise ValueError("Only https:// URLs are accepted")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("Only github.com repositories are supported")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("URL contains unsupported components")
    m = REPO_PATH_RE.match(parsed.path)
    if not m:
        raise ValueError("Expected a repository URL like https://github.com/owner/repo")
    owner, name = m.group(1), m.group(2)
    if owner.startswith(".") or name.startswith("."):
        raise ValueError("Invalid repository path")
    return GitHubRepo(owner=owner, name=name, clone_url=f"https://github.com/{owner}/{name}.git")


def clone_repo(repo: GitHubRepo, dest_dir: str, timeout: int | None = None) -> None:
    """Full-history clone (the forensics read the commit graph)."""
    timeout = timeout or int(os.environ.get("LASTCOMMIT_CLONE_TIMEOUT", DEFAULT_CLONE_TIMEOUT))
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")  # never hang on credential prompts
    try:
        subprocess.run(
            ["git", "clone", "--quiet", "--config", "credential.helper=", repo.clone_url, dest_dir],
            check=True, capture_output=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Cloning timed out after {timeout}s") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip().splitlines()
        msg = detail[-1] if detail else "clone failed"
        raise RuntimeError(f"Could not clone {repo.clone_url}: {msg}") from exc
