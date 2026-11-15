"""Storage for jobs.

All data lives in ONE JSON file. Two backends share the same interface:

* LocalStore  - a file on disk (tests, local runs).
* GitHubStore - the same file on a separate `data` branch of your repo, read and
  written through the GitHub API. Using a separate branch means data commits never
  restart the Streamlit app (it only watches `main`).

Writes use optimistic locking: if someone else saved first (GitHub returns 409),
we reload and re-apply the change, so GitHub Actions and the web app can never
overwrite each other's work.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

DATA_PATH = "data/jobs.json"

# Job statuses (the whole flow)
NEW = "new"                    # found, not scored yet
LOW_MATCH = "low_match"        # scored below min_score
REVIEW = "review"              # waiting for you to approve
APPROVED = "approved"          # approved, waiting to be sent
SENT = "sent"                  # email sent
APPLIED_MANUALLY = "applied_manually"  # you applied on the website yourself
REJECTED = "rejected"          # you skipped it
REPLIED = "replied"            # recruiter replied
FAILED = "failed"              # sending failed (see last_error)

ALL_STATUSES = [NEW, REVIEW, APPROVED, SENT, APPLIED_MANUALLY, REPLIED, LOW_MATCH, REJECTED, FAILED]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def empty_data() -> dict[str, Any]:
    return {"version": 1, "jobs": {}, "runs": []}


def set_status(job: dict, status: str, note: str = "") -> None:
    job["status"] = status
    job["updated_at"] = now_iso()
    job.setdefault("history", []).append({"at": job["updated_at"], "status": status, "note": note})


Mutator = Callable[[dict[str, Any]], Any]


class LocalStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_data()
        return json.loads(self.path.read_text(encoding="utf-8"))

    def update(self, mutator: Mutator, message: str = "update jobs") -> Any:
        data = self.load()
        result = mutator(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        return result


class GitHubStore:
    API = "https://api.github.com"

    def __init__(self, repo: str, token: str, branch: str = "data", path: str = DATA_PATH,
                 session: requests.Session | None = None):
        if not repo or not token:
            raise ValueError("GitHubStore needs GH_REPO (owner/name) and GH_TOKEN")
        self.repo, self.branch, self.path = repo, branch, path
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}",
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    # -- helpers -----------------------------------------------------------
    def _url(self, p: str) -> str:
        return f"{self.API}/repos/{self.repo}/{p}"

    def ensure_branch(self) -> None:
        if getattr(self, "_branch_ok", False):
            return
        r = self.s.get(self._url(f"branches/{self.branch}"), timeout=30)
        if r.status_code == 200:
            self._branch_ok = True
            return
        repo = self.s.get(f"{self.API}/repos/{self.repo}", timeout=30)
        repo.raise_for_status()
        default = repo.json()["default_branch"]
        head = self.s.get(self._url(f"git/ref/heads/{default}"), timeout=30)
        head.raise_for_status()
        sha = head.json()["object"]["sha"]
        c = self.s.post(self._url("git/refs"), json={"ref": f"refs/heads/{self.branch}", "sha": sha}, timeout=30)
        if c.status_code not in (201, 422):  # 422 = already exists (race)
            c.raise_for_status()
        self._branch_ok = True

    def _get(self) -> tuple[dict[str, Any], str | None]:
        r = self.s.get(self._url(f"contents/{self.path}"), params={"ref": self.branch}, timeout=30)
        if r.status_code == 404:
            return empty_data(), None
        r.raise_for_status()
        meta = r.json()
        if meta.get("content"):
            raw = base64.b64decode(meta["content"])
        else:  # files over 1 MB come without inline content
            raw = self.s.get(self._url(f"contents/{self.path}"), params={"ref": self.branch},
                             headers={"Accept": "application/vnd.github.raw"}, timeout=60).content
        return json.loads(raw.decode("utf-8")), meta["sha"]

    # -- public API --------------------------------------------------------
    def load(self) -> dict[str, Any]:
        data, _ = self._get()
        return data

    def update(self, mutator: Mutator, message: str = "update jobs", retries: int = 5) -> Any:
        self.ensure_branch()
        for attempt in range(retries):
            data, sha = self._get()
            result = mutator(data)
            body = {
                "message": f"data: {message}",
                "content": base64.b64encode(json.dumps(data, indent=1, ensure_ascii=False).encode()).decode(),
                "branch": self.branch,
            }
            if sha:
                body["sha"] = sha
            r = self.s.put(self._url(f"contents/{self.path}"), json=body, timeout=60)
            if r.status_code in (200, 201):
                return result
            if r.status_code in (409, 422):  # someone else saved first -> retry
                time.sleep(1 + attempt)
                continue
            r.raise_for_status()
        raise RuntimeError("Could not save data after several retries (conflicts)")

    def dispatch_workflow(self, workflow_file: str, inputs: dict[str, str] | None = None, ref: str = "main") -> None:
        r = self.s.post(self._url(f"actions/workflows/{workflow_file}/dispatches"),
                        json={"ref": ref, "inputs": inputs or {}}, timeout=30)
        r.raise_for_status()

    def latest_runs(self, workflow_file: str, n: int = 5) -> list[dict]:
        r = self.s.get(self._url(f"actions/workflows/{workflow_file}/runs"), params={"per_page": n}, timeout=30)
        if r.status_code != 200:
            return []
        return [{"status": x["status"], "conclusion": x["conclusion"], "created_at": x["created_at"],
                 "url": x["html_url"], "event": x["event"]} for x in r.json().get("workflow_runs", [])]


def make_store(local_path: str | Path | None = None):
    """GitHubStore when GH_REPO + GH_TOKEN are set, else a local file."""
    from .config import ROOT, secret
    repo = secret("GH_REPO") or secret("GITHUB_REPOSITORY")
    token = secret("GH_TOKEN") or secret("GITHUB_TOKEN")
    if repo and token and not local_path:
        return GitHubStore(repo, token, branch=secret("DATA_BRANCH", "data"))
    return LocalStore(local_path or ROOT / DATA_PATH)
