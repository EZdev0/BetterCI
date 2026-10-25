"""Bounded least-privilege GitHub REST client for CI diagnostics."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_VERSION = "2026-03-10"
MAX_COMMENT_CHARS = 24_000
MAX_RUNS = 5
MAX_CANDIDATE_RUNS = 50
MAX_JOBS = 50
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 20
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubAPIError(RuntimeError):
    """A bounded GitHub REST operation failed without exposing response content."""

    def __init__(self, operation: str, status: int | None) -> None:
        self.operation = operation
        self.status = status
        label = f"HTTP {status}" if status is not None else "network error"
        super().__init__(f"GitHub API {operation} failed ({label})")


class GitHubAPI:
    """Small client for the exact read-and-comment REST calls used here."""

    def __init__(self, repository: str, token: str, api_url: str = "https://api.github.com") -> None:
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("GITHUB_REPOSITORY must be owner/name")
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        if api_url != "https://api.github.com":
            raise ValueError("GITHUB_API_URL must be https://api.github.com")
        self.repository = repository
        self.token = token
        self.api_url = api_url

    def _request(
        self,
        operation: str,
        path: str,
        *,
        query: dict[str, str | int] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if not path.startswith("/") or ".." in path:
            raise ValueError("invalid GitHub API path")
        url = self.api_url + path
        if query:
            url += "?" + urlencode(query)
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(  # noqa: S310 - URL is built from the fixed HTTPS API origin
            url,
            data=body,
            method="POST" if payload is not None else "GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "BetterCI-read-only-diagnostics",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed HTTPS API origin
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise GitHubAPIError(operation, exc.code) from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise GitHubAPIError(operation, None) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GitHubAPIError(operation, None)
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise GitHubAPIError(operation, None) from exc

    @property
    def _repo_path(self) -> str:
        return f"/repos/{self.repository}"

    def pull_request(self, number: int) -> dict[str, Any]:
        data = self._request("read pull request", f"{self._repo_path}/pulls/{number}")
        if not isinstance(data, dict):
            raise GitHubAPIError("read pull request", None)
        return data

    def workflow_runs(self, head_sha: str) -> list[dict[str, Any]]:
        data = self._request(
            "list workflow runs",
            f"{self._repo_path}/actions/runs",
            query={"head_sha": head_sha, "per_page": MAX_CANDIDATE_RUNS},
        )
        runs = data.get("workflow_runs") if isinstance(data, dict) else None
        if not isinstance(runs, list):
            raise GitHubAPIError("list workflow runs", None)
        return [run for run in runs[:MAX_CANDIDATE_RUNS] if isinstance(run, dict)]

    def workflow_run(self, run_id: int) -> dict[str, Any]:
        data = self._request("read workflow run", f"{self._repo_path}/actions/runs/{run_id}")
        if not isinstance(data, dict):
            raise GitHubAPIError("read workflow run", None)
        return data

    def workflow_jobs(self, run_id: int) -> list[dict[str, Any]]:
        data = self._request(
            "list workflow jobs",
            f"{self._repo_path}/actions/runs/{run_id}/jobs",
            query={"filter": "latest", "per_page": MAX_JOBS},
        )
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            raise GitHubAPIError("list workflow jobs", None)
        return [job for job in jobs[:MAX_JOBS] if isinstance(job, dict)]

    def post_comment(self, issue_number: int, body: str) -> None:
        self._request(
            "post diagnostic comment",
            f"{self._repo_path}/issues/{issue_number}/comments",
            payload={"body": body[:MAX_COMMENT_CHARS]},
        )
