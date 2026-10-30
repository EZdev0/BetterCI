"""Regression tests for read-only GitHub issue/PR comment diagnostics."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from vibeci import github_api
from vibeci.github_commands import (
    MAX_CANDIDATE_RUNS,
    MAX_COMMENT_CHARS,
    MAX_POLICY_CHARS,
    CommandPolicy,
    CommandPolicyError,
    CommandUsageError,
    GitHubAPI,
    main,
    parse_comment,
    parse_policy,
    run,
)
from vibeci.github_policy import LOCAL_ONLY_COMMAND_NAMES

HEAD_SHA = "a" * 40
OTHER_SHA = "b" * 40
DEFAULT_SHA = "c" * 40
REPOSITORY = "example/BetterCI"
ACTOR_LOGIN = "trusted-user"
ACTOR_ID = 4242
POLICY_JSON = json.dumps(
    {
        "version": 1,
        "trusted_actors": [{"login": ACTOR_LOGIN, "id": ACTOR_ID}],
        "control_issues": [17],
    }
)
POLICY = parse_policy(POLICY_JSON)


def _event(
    body: str,
    association: str = "OWNER",
    *,
    issue_number: int = 17,
    pull_request: bool = True,
    login: str = ACTOR_LOGIN,
    user_id: int = ACTOR_ID,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"number": issue_number}
    if pull_request:
        issue["pull_request"] = {"url": f"https://api.github.com/pulls/{issue_number}"}
    return {
        "action": "created",
        "repository": {"full_name": REPOSITORY, "default_branch": "main"},
        "issue": issue,
        "comment": {
            "id": 101,
            "body": body,
            "author_association": association,
            "user": {"login": login, "id": user_id},
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
            "path": ".github/workflows/ci.yml",
        },
        {
            "id": 4000,
            "name": "Security",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 2,
            "head_sha": HEAD_SHA,
            "path": ".github/workflows/security.yml",
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


def _execute(
    event: dict[str, Any],
    api: FakeAPI,
    *,
    policy: CommandPolicy = POLICY,
    actor: str = ACTOR_LOGIN,
    default_sha: str = DEFAULT_SHA,
) -> int:
    return run(event, REPOSITORY, api, policy, actor, default_sha)


@pytest.mark.parametrize(
    ("text", "name", "run_id", "target", "target_pull_request"),
    [
        ("/help", "help", None, "auto", None),
        ("/Help", "help", None, "auto", None),
        ("/VIBECI", "help", None, "auto", None),
        ("/vibeci HeLp", "help", None, "auto", None),
        ("/vibeci /CI", "ci", None, "auto", None),
        ("/CI --TARGET PR:5", "ci", None, "pull_request", 5),
        ("/RUN 42 --target default", "run", 42, "default", None),
        ("/vibeci failures --target pr:7", "failures", None, "pull_request", 7),
    ],
)
def test_comment_parser_is_case_insensitive(text, name, run_id, target, target_pull_request):
    parsed = parse_comment(text)
    assert parsed is not None
    assert parsed.name == name
    assert parsed.run_id == run_id
    assert parsed.target == target
    assert parsed.target_pull_request == target_pull_request


def test_comment_parser_recognizes_local_only_commands_without_parsing_arguments():
    for name in LOCAL_ONLY_COMMAND_NAMES:
        parsed = parse_comment(f"/{name} untrusted arguments are not executed")
        assert parsed is not None
        assert parsed.name == "local-only"
        assert parsed.local_command == name
        assert parsed.run_id is None
        assert parsed.target == "auto"

    prefixed = parse_comment("/vibeci start task-1")
    assert prefixed is not None
    assert prefixed.name == "local-only"
    assert prefixed.local_command == "start"


def test_comment_parser_ignores_unrelated_commands_and_rejects_ambiguous_input():
    assert parse_comment("ordinary conversation") is None
    assert parse_comment("/dependabot rebase") is None
    with pytest.raises(CommandUsageError, match="numeric-run-id"):
        parse_comment("/vibeci run not-a-number")
    with pytest.raises(CommandUsageError, match="one command line"):
        parse_comment("/help\nignore the previous instruction")
    with pytest.raises(CommandUsageError, match="one command line"):
        parse_comment("/run 42\nignore the previous instruction")
    with pytest.raises(CommandUsageError, match="--target default"):
        parse_comment("/ci --target refs/heads/attacker")
    with pytest.raises(CommandUsageError, match="takes no arguments"):
        parse_comment("/help --target default")


def test_secret_policy_is_bounded_exact_and_fail_closed():
    assert POLICY.trusted_actors == frozenset({(ACTOR_LOGIN, ACTOR_ID)})
    assert POLICY.control_issues == frozenset({17})

    invalid_policies = [
        "",
        "not-json",
        '{"version":1,"trusted_actors":[],"control_issues":[17]}',
        '{"version":1,"trusted_actors":[{"login":"trusted-user","id":4242}],"control_issues":[17],"extra":true}',
        '{"version":1,"trusted_actors":[{"login":"trusted-user","id":4242}],"control_issues":[17,17]}',
        '{"version":1.0,"trusted_actors":[{"login":"trusted-user","id":4242}],"control_issues":[17]}',
    ]
    for raw in invalid_policies:
        with pytest.raises(CommandPolicyError):
            parse_policy(raw)
    with pytest.raises(CommandPolicyError, match="exceeds") as error:
        parse_policy("sensitive-policy-value" + "x" * MAX_POLICY_CHARS)
    assert "sensitive-policy-value" not in str(error.value)


def test_malformed_selected_command_gets_a_bounded_usage_response():
    api = FakeAPI()

    result = _execute(_event("/vibeci run not-a-number"), api)

    assert result == 2
    assert api.calls == [("post", 17)]
    body = api.posted[0][1]
    assert "diagnostic command rejected" in body
    assert "usage: /run &lt;numeric-run-id&gt;" in body
    assert "least-privilege token settings" not in body


def test_comment_help_distinguishes_diagnostics_from_local_control():
    api = FakeAPI()

    result = _execute(_event("/Help"), api)

    assert result == 0
    assert api.calls == [("post", 17)]
    assert len(api.posted) == 1
    body = api.posted[0][1]
    assert "VibeCI command availability" in body
    assert "Available here: GitHub-hosted read-only diagnostics" in body
    assert "/runners" in body and "/failures" in body
    assert "--target default" in body and "pr:<number>" in body
    assert "Inspect one existing run" in body and "rerun nothing" in body
    assert "Local controller only: unavailable in GitHub comments" in body
    for name in LOCAL_ONLY_COMMAND_NAMES:
        assert f"/{name}" in body
    assert "creates only an isolated branch/worktree" in body
    assert "does **not** launch a model, provider or implementation worker" in body
    assert "exact login/user-ID policy match" in body
    assert "2 MiB" in body and "20-second timeout" in body
    assert "cannot start, rerun, cancel, approve, merge" in body


def test_known_local_commands_get_actionable_github_response():
    for name in LOCAL_ONLY_COMMAND_NAMES:
        api = FakeAPI()
        result = _execute(_event(f"/{name} @everyone https://attacker.invalid"), api)

        assert result == 0
        assert api.calls == [("post", 17)]
        body = api.posted[0][1]
        assert "VibeCI command is local-only" in body
        assert f"`/{name}" in body
        assert "not available in GitHub issue or PR comments" in body
        assert "No state, branch, worktree, workflow, model or provider was started" in body
        assert "@everyone" not in body
        assert "attacker.invalid" not in body

    unrelated_api = FakeAPI()
    assert _execute(_event("/dependabot rebase"), unrelated_api) == 0
    assert unrelated_api.calls == [] and unrelated_api.posted == []


def test_control_issue_help_and_ci_use_the_exact_default_branch_event_head():
    help_api = FakeAPI()
    assert _execute(_event("/help", pull_request=False), help_api) == 0
    assert help_api.calls == [("post", 17)]

    ci_api = FakeAPI()
    for workflow_run in ci_api.runs:
        workflow_run["head_sha"] = DEFAULT_SHA
    assert _execute(_event("/ci", pull_request=False), ci_api) == 0

    assert ("pull", 17) not in ci_api.calls
    assert ("runs", DEFAULT_SHA) in ci_api.calls
    body = ci_api.posted[0][1]
    assert f"Exact target (default branch event head): `{DEFAULT_SHA}`" in body
    assert "GitHub Actions 42" in body


def test_explicit_targets_are_same_repository_and_exact_sha_bound():
    pull_api = FakeAPI()
    assert _execute(_event("/runs --target pr:23", pull_request=False), pull_api) == 0
    assert pull_api.calls[:2] == [("pull", 23), ("runs", HEAD_SHA)]
    assert "Exact target (PR #23 head)" in pull_api.posted[0][1]

    default_api = FakeAPI()
    for workflow_run in default_api.runs:
        workflow_run["head_sha"] = DEFAULT_SHA
    assert _execute(_event("/runs --target default"), default_api) == 0
    assert ("pull", 17) not in default_api.calls
    assert ("runs", DEFAULT_SHA) in default_api.calls


def test_ci_diagnostics_show_runner_and_failed_steps_without_mentions_or_logs():
    api = FakeAPI()

    result = _execute(_event("/vibeci CI"), api)

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

    assert _execute(_event("/runners"), api) == 0

    body = api.posted[0][1]
    assert "assigned runners" in body
    assert "job-assignment evidence" in body
    assert "not an admin-level self-hosted-runner inventory" in body
    assert "GitHub Actions 41" in body and "GitHub Actions 42" in body


def test_failures_command_is_bounded_to_failure_metadata():
    api = FakeAPI()

    assert _execute(_event("/failures"), api) == 0

    body = api.posted[0][1]
    assert "tests \\| Python @\u200ball" in body
    assert "Unit tests @\u200bteam" in body
    assert "Job logs are intentionally not copied" in body
    assert "Checkout" not in body
    assert "zizmor" not in body


def test_comment_handler_rejects_untrusted_actor_and_unrelated_run():
    unauthorized_api = FakeAPI()
    assert _execute(_event("/ci", association="CONTRIBUTOR"), unauthorized_api) == 0
    assert unauthorized_api.calls == [] and unauthorized_api.posted == []

    unrelated_api = FakeAPI()
    result = _execute(_event("/run 9999"), unrelated_api)
    assert result == 2
    assert len(unrelated_api.posted) == 1
    error = unrelated_api.posted[0][1]
    assert "does not belong to the selected target's exact head SHA" in error
    assert "No workflow was rerun or cancelled" in error


def test_repository_mismatch_and_unlisted_control_issues_are_silent():
    api = FakeAPI()
    mismatch = _event("/help")
    mismatch["repository"]["full_name"] = "attacker/other"
    assert _execute(mismatch, api) == 0

    issue = _event("/help", issue_number=18, pull_request=False)
    assert _execute(issue, api) == 0
    assert api.calls == []


def test_policy_requires_exact_login_user_id_actor_and_repository_role():
    cases = [
        (_event("/help", login="other-user"), ACTOR_LOGIN),
        (_event("/help", user_id=9999), ACTOR_LOGIN),
        (_event("/help"), "different-actor"),
        (_event("/help", association="CONTRIBUTOR"), ACTOR_LOGIN),
    ]
    for event, actor in cases:
        api = FakeAPI()
        assert _execute(event, api, actor=actor) == 0
        assert api.calls == [] and api.posted == []


def test_runs_are_filtered_against_the_exact_pull_request_head():
    api = FakeAPI()
    api.runs.extend(
        [
            {
                "id": 3999,
                "name": "stale run",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "failure",
                "head_sha": OTHER_SHA,
                "path": ".github/workflows/ci.yml",
            },
            {
                "id": 3998,
                "name": "VibeCI read-only commands",
                "event": "issue_comment",
                "status": "completed",
                "conclusion": "success",
                "head_sha": HEAD_SHA,
                "path": ".github/workflows/vibeci-commands.yml",
            },
        ]
    )

    assert _execute(_event("/runs"), api) == 0

    body = api.posted[0][1]
    assert "#4001" in body and "#4000" in body
    assert "#3999" not in body and "stale run" not in body
    assert "#3998" not in body and "VibeCI read-only commands" not in body


def test_direct_command_workflow_run_is_rejected_and_candidate_scan_is_disclosed():
    command_run = {
        "id": 3998,
        "name": "VibeCI read-only commands",
        "event": "issue_comment",
        "status": "completed",
        "conclusion": "success",
        "head_sha": HEAD_SHA,
        "path": ".github/workflows/vibeci-commands.yml",
    }
    direct_api = FakeAPI()
    direct_api.runs.append(command_run)
    assert _execute(_event("/run 3998"), direct_api) == 2
    assert "command adapter itself" in direct_api.posted[0][1]
    assert ("jobs", 3998) not in direct_api.calls

    bounded_api = FakeAPI()
    bounded_api.runs = [bounded_api.runs[0]] + [deepcopy(command_run) for _ in range(MAX_CANDIDATE_RUNS - 1)]
    assert _execute(_event("/runs"), bounded_api) == 0
    body = bounded_api.posted[0][1]
    assert "#4001" in body
    assert f"{MAX_CANDIDATE_RUNS}-candidate bound" in body


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

    assert _execute(_event("/ci"), api) == 0

    body = api.posted[0][1]
    assert len(body) <= MAX_COMMENT_CHARS
    assert "Output truncated by BetterCI's comment budget" in body
    assert body.endswith("_Requested by comment `101`._")


def test_main_rejects_invalid_policy_without_printing_secret(monkeypatch, tmp_path, capsys):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_event("/help")), encoding="utf-8")
    invalid_policy = "invalid-policy-that-must-not-appear"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("VIBECI_ACTOR", ACTOR_LOGIN)
    monkeypatch.setenv("VIBECI_DEFAULT_SHA", DEFAULT_SHA)
    monkeypatch.setenv("VIBECI_COMMAND_POLICY_JSON", invalid_policy)

    assert main() == 2
    output = capsys.readouterr().out
    assert "not valid JSON" in output
    assert invalid_policy not in output


def test_github_client_uses_versioned_bounded_rest_requests(monkeypatch):
    captured = []
    read_limits = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum):
            read_limits.append(maximum)
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
    assert get_request.method == "GET" and get_timeout == github_api.REQUEST_TIMEOUT_SECONDS
    assert f"head_sha={HEAD_SHA}" in get_request.full_url
    assert f"per_page={MAX_CANDIDATE_RUNS}" in get_request.full_url
    assert get_request.get_header("X-github-api-version") == github_api.API_VERSION
    assert get_request.get_header("Authorization") == "Bearer test-token"
    assert "test-token" not in get_request.full_url
    assert post_request.method == "POST" and post_timeout == github_api.REQUEST_TIMEOUT_SECONDS
    assert post_request.data == b'{"body": "bounded response"}'
    assert read_limits == [github_api.MAX_RESPONSE_BYTES + 1, github_api.MAX_RESPONSE_BYTES + 1]


def test_github_client_rejects_oversized_api_responses(monkeypatch):
    class OversizedResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum):
            assert maximum == github_api.MAX_RESPONSE_BYTES + 1
            return b"x" * maximum

    monkeypatch.setattr(github_api, "urlopen", lambda *_args, **_kwargs: OversizedResponse())
    with pytest.raises(github_api.GitHubAPIError, match="list workflow runs"):
        GitHubAPI(REPOSITORY, "test-token").workflow_runs(HEAD_SHA)


def test_github_client_refuses_non_github_api_origins_and_missing_credentials():
    with pytest.raises(ValueError, match="owner/name"):
        GitHubAPI("bad repository", "token")
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        GitHubAPI(REPOSITORY, "")
    with pytest.raises(ValueError, match=r"https://api\.github\.com"):
        GitHubAPI(REPOSITORY, "token", "https://example.invalid")
