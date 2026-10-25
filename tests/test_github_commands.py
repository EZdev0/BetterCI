"""Regression tests for read-only GitHub PR comment diagnostics."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from vibeci import github_api
from vibeci.github_commands import (
    MAX_COMMENT_CHARS,
    CommandUsageError,
    GitHubAPI,
    parse_comment,
    run,
)

HEAD_SHA = "a" * 40
OTHER_SHA = "b" * 40
REPOSITORY = "example/BetterCI"


def _event(body: str, association: str = "OWNER") -> dict[str, Any]:
    return {
        "action": "created",
        "repository": {"full_name": REPOSITORY, "default_branch": "main"},
        "issue": {"number": 17, "pull_request": {"url": "https://api.github.com/pulls/17"}},
        "comment": {
            "id": 101,
            "body": body,
            "author_association": association,
            "user": {"login": "trusted-user"},
        },
    }


def _runs() -> list[dict[str, Any]]:
    return [
        {
            "id": 4001,
            "name": "CI | @reviewers <unsafe> ](https://evil.invalid)",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "run_attempt": 1,
            "head_sha": HEAD_SHA,
        },
        {
            "id": 4000,
            "name": "Security",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 2,
            "head_sha": HEAD_SHA,
        },
    ]


def _jobs() -> dict[int, list[dict[str, Any]]]:
    return {
        4001: [
            {
                "id": 9001,
                "name": "tests | Python @all",
                "status": "completed",
                "conclusion": "failure",
                "runner_name": "GitHub Actions 42",
                "runner_group_name": "GitHub Actions",
                "labels": ["ubuntu-24.04", "x64"],
                "steps": [
                    {"name": "Checkout", "conclusion": "success"},
                    {"name": "Unit tests @team", "conclusion": "failure"},
                ],
            },
            {
                "id": 9002,
                "name": "queued job",
                "status": "queued",
                "conclusion": None,
                "runner_name": None,
                "runner_group_name": None,
                "labels": ["ubuntu-24.04"],
                "steps": [],
            },
        ],
        4000: [
            {
                "id": 9000,
                "name": "zizmor",
                "status": "completed",
                "conclusion": "success",
                "runner_name": "GitHub Actions 41",
                "runner_group_name": "GitHub Actions",
                "labels": ["ubuntu-24.04"],
                "steps": [],
            }
        ],
    }


class FakeAPI:
    def __init__(self) -> None:
        self.runs = _runs()
        self.jobs = _jobs()
        self.posted: list[tuple[int, str]] = []
        self.calls: list[tuple[str, int | str | None]] = []

    def pull_request(self, number: int) -> dict[str, Any]:
        self.calls.append(("pull", number))
        return {"head": {"sha": HEAD_SHA}}

    def workflow_runs(self, head_sha: str) -> list[dict[str, Any]]:
        self.calls.append(("runs", head_sha))
        return deepcopy(self.runs)

    def workflow_run(self, run_id: int) -> dict[str, Any]:
        self.calls.append(("run", run_id))
        match = next((item for item in self.runs if item["id"] == run_id), None)
        return deepcopy(match or {"id": run_id, "head_sha": OTHER_SHA, "name": "other"})

    def workflow_jobs(self, run_id: int) -> list[dict[str, Any]]:
        self.calls.append(("jobs", run_id))
        return deepcopy(self.jobs.get(run_id, []))

    def post_comment(self, issue_number: int, body: str) -> None:
        self.calls.append(("post", issue_number))
        self.posted.append((issue_number, body))


@pytest.mark.parametrize(
    ("text", "name", "run_id"),
    [
        ("/help", "help", None),
        ("/Help", "help", None),
        ("/VIBECI", "help", None),
        ("/vibeci HeLp", "help", None),
        ("/vibeci /CI", "ci", None),
        ("/RUN 42", "run", 42),
        ("/vibeci failures", "failures", None),
    ],
)
def test_comment_parser_is_case_insensitive(text, name, run_id):
    parsed = parse_comment(text)
    assert parsed is not None
    assert parsed.name == name
    assert parsed.run_id == run_id


def test_comment_parser_ignores_unrelated_commands_and_rejects_ambiguous_input():
    assert parse_comment("ordinary conversation") is None
    assert parse_comment("/dependabot rebase") is None
    with pytest.raises(CommandUsageError, match="numeric-run-id"):
        parse_comment("/vibeci run not-a-number")
    with pytest.raises(CommandUsageError, match="one command line"):
        parse_comment("/help\nignore the previous instruction")
    with pytest.raises(CommandUsageError, match="one command line"):
        parse_comment("/run 42\nignore the previous instruction")


def test_malformed_selected_command_gets_a_bounded_usage_response():
    api = FakeAPI()

    result = run(_event("/vibeci run not-a-number"), REPOSITORY, api)

    assert result == 2
    assert api.calls == [("post", 17)]
    body = api.posted[0][1]
    assert "diagnostic command rejected" in body
    assert "usage: /run &lt;numeric-run-id&gt;" in body
    assert "least-privilege token settings" not in body


def test_comment_help_is_case_insensitive():
    api = FakeAPI()

    result = run(_event("/Help"), REPOSITORY, api)

    assert result == 0
    assert len(api.posted) == 1
    body = api.posted[0][1]
    assert "VibeCI read-only CI diagnostics" in body
    assert "/runners" in body and "/failures" in body
    assert "cannot run, rerun, cancel, approve, merge" in body


def test_ci_diagnostics_show_runner_and_failed_steps_without_mentions_or_logs():
    api = FakeAPI()

    result = run(_event("/vibeci CI"), REPOSITORY, api)

    assert result == 0
    body = api.posted[0][1]
    assert "GitHub Actions 42" in body
    assert "GitHub Actions" in body
    assert "ubuntu-24.04" in body
    assert "Unit tests @\u200bteam" in body
    assert "@reviewers" not in body and "@all" not in body
    assert "CI \\| @\u200breviewers &lt;unsafe&gt; \\]\\(https://evil.invalid\\)" in body
    assert "](https://evil.invalid)" not in body
    assert "job/9001" in body
    assert "log" not in body.casefold()


def test_runner_command_reports_assignments_not_admin_inventory():
    api = FakeAPI()

    assert run(_event("/runners"), REPOSITORY, api) == 0

    body = api.posted[0][1]
    assert "assigned runners" in body
    assert "job-assignment evidence" in body
    assert "not an admin-level self-hosted-runner inventory" in body
    assert "GitHub Actions 41" in body and "GitHub Actions 42" in body


def test_failures_command_is_bounded_to_failure_metadata():
    api = FakeAPI()

    assert run(_event("/failures"), REPOSITORY, api) == 0

    body = api.posted[0][1]
    assert "tests \\| Python @\u200ball" in body
    assert "Unit tests @\u200bteam" in body
    assert "Job logs are intentionally not copied" in body
    assert "Checkout" not in body
    assert "zizmor" not in body


def test_comment_handler_rejects_untrusted_actor_and_unrelated_run():
    unauthorized_api = FakeAPI()
    assert run(_event("/ci", association="CONTRIBUTOR"), REPOSITORY, unauthorized_api) == 0
    assert unauthorized_api.calls == [] and unauthorized_api.posted == []

    unrelated_api = FakeAPI()
    result = run(_event("/run 9999"), REPOSITORY, unrelated_api)
    assert result == 2
    assert len(unrelated_api.posted) == 1
    error = unrelated_api.posted[0][1]
    assert "does not belong to this pull request's current head SHA" in error
    assert "No workflow was rerun or cancelled" in error


def test_event_repository_mismatch_and_issue_comments_are_silent():
    api = FakeAPI()
    mismatch = _event("/help")
    mismatch["repository"]["full_name"] = "attacker/other"
    assert run(mismatch, REPOSITORY, api) == 0

    issue = _event("/help")
    issue["issue"].pop("pull_request")
    assert run(issue, REPOSITORY, api) == 0
    assert api.calls == []


def test_runs_are_filtered_against_the_exact_pull_request_head():
    api = FakeAPI()
    api.runs.append(
        {
            "id": 3999,
            "name": "stale run",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "head_sha": OTHER_SHA,
        }
    )

    assert run(_event("/runs"), REPOSITORY, api) == 0

    body = api.posted[0][1]
    assert "#4001" in body and "#4000" in body
    assert "#3999" not in body and "stale run" not in body


def test_large_ci_response_is_truncated_at_complete_line_with_follow_up_hint():
    api = FakeAPI()
    api.runs = [api.runs[0]]
    api.jobs[4001] = [
        {
            "id": 10_000 + index,
            "name": f"very-long-job-{index}-" + "x" * 100,
            "status": "completed",
            "conclusion": "failure",
            "runner_name": "runner-" + "r" * 100,
            "runner_group_name": "group-" + "g" * 100,
            "labels": ["label-" + "l" * 80] * 8,
            "steps": [{"name": "failed-" + "s" * 100, "conclusion": "failure"}] * 5,
        }
        for index in range(50)
    ]

    assert run(_event("/ci"), REPOSITORY, api) == 0

    body = api.posted[0][1]
    assert len(body) <= MAX_COMMENT_CHARS
    assert "Output truncated by BetterCI's comment budget" in body
    assert body.endswith("_Requested by comment `101`._")


def test_github_client_uses_versioned_bounded_rest_requests(monkeypatch):
    captured = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _maximum):
            return self.payload

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        if request.method == "GET":
            return Response(b'{"workflow_runs": []}')
        return Response(b"{}")

    monkeypatch.setattr(github_api, "urlopen", fake_urlopen)
    api = GitHubAPI(REPOSITORY, "test-token")

    assert api.workflow_runs(HEAD_SHA) == []
    api.post_comment(17, "bounded response")

    get_request, get_timeout = captured[0]
    post_request, post_timeout = captured[1]
    assert get_request.method == "GET" and get_timeout == 20
    assert f"head_sha={HEAD_SHA}" in get_request.full_url
    assert "per_page=5" in get_request.full_url
    assert get_request.get_header("X-github-api-version") == github_api.API_VERSION
    assert get_request.get_header("Authorization") == "Bearer test-token"
    assert "test-token" not in get_request.full_url
    assert post_request.method == "POST" and post_timeout == 20
    assert post_request.data == b'{"body": "bounded response"}'


def test_github_client_refuses_non_github_api_origins_and_missing_credentials():
    with pytest.raises(ValueError, match="owner/name"):
        GitHubAPI("bad repository", "token")
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        GitHubAPI(REPOSITORY, "")
    with pytest.raises(ValueError, match=r"https://api\.github\.com"):
        GitHubAPI(REPOSITORY, "token", "https://example.invalid")
