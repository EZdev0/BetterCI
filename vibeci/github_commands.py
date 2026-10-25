"""Read-only GitHub PR comment commands for CI and runner diagnostics.

The module intentionally uses only the Python standard library so the
``issue_comment`` workflow can execute reviewed default-branch code without
installing dependencies or restoring a cache. Comment text is parsed as data;
it never becomes a shell command, ref, URL, or API path.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any

from .github_api import MAX_COMMENT_CHARS, MAX_JOBS, GitHubAPI, GitHubAPIError

MAX_COMMAND_CHARS = 200
MAX_FAILED_STEPS = 5
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_COMMAND_NAMES = frozenset({"help", "ci", "runs", "run", "runners", "failures"})
_BARE_COMMANDS = frozenset(f"/{name}" for name in _COMMAND_NAMES)
_TERMINAL_FAILURES = frozenset({"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"})
_HEAD_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
_RESPONSE_MARKER = "<!-- vibeci-read-only-ci-diagnostics -->"
_PERMISSION_HELP = (
    "https://docs.github.com/en/actions/security-for-github-actions/security-guides/"
    "automatic-token-authentication#modifying-the-permissions-for-the-github_token"
)


class CommandUsageError(ValueError):
    """The comment selected this adapter but did not match its bounded grammar."""


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    run_id: int | None = None


@dataclass(frozen=True)
class CommandEvent:
    repository: str
    issue_number: int
    comment_id: int
    head_sha: str
    command: ParsedCommand


def _targets_adapter(line: str) -> bool:
    first = line.split(maxsplit=1)[0].casefold() if line.split() else ""
    return first == "/vibeci" or first in _BARE_COMMANDS


def _command_tokens(body: str) -> list[str] | None:
    if not isinstance(body, str):
        return None
    first_line = body.lstrip().splitlines()[0].strip() if body.strip() else ""
    if len(body) > MAX_COMMAND_CHARS:
        if _targets_adapter(first_line):
            raise CommandUsageError(f"command exceeds {MAX_COMMAND_CHARS} characters")
        return None
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        if _targets_adapter(lines[0]):
            raise CommandUsageError("use exactly one command line")
        return None
    tokens = lines[0].split()
    return tokens if tokens and tokens[0].isascii() else None


def _normalized_command(tokens: list[str]) -> tuple[str, list[str], bool] | None:
    prefixed = tokens[0].casefold() == "/vibeci"
    if prefixed:
        tokens = tokens[1:]
        if not tokens:
            return "help", [], True
        tokens[0] = tokens[0].removeprefix("/")
    else:
        command_token = tokens[0].casefold()
        if command_token not in _BARE_COMMANDS:
            return None
        tokens[0] = command_token.removeprefix("/")
    return tokens[0].casefold(), tokens[1:], prefixed


def parse_comment(body: str) -> ParsedCommand | None:
    """Parse one case-insensitive command line; unrelated slash commands are ignored."""
    tokens = _command_tokens(body)
    if tokens is None:
        return None
    normalized = _normalized_command(tokens)
    if normalized is None:
        return None
    name, arguments, prefixed = normalized
    if not name.isascii() or name not in _COMMAND_NAMES:
        if prefixed:
            raise CommandUsageError(f"unknown read-only command: {name}")
        return None
    if name != "run":
        if arguments:
            raise CommandUsageError(f"/{name} takes no arguments")
        return ParsedCommand(name)
    if len(arguments) != 1 or not arguments[0].isascii() or not arguments[0].isdigit():
        raise CommandUsageError("usage: /run <numeric-run-id>")
    run_id = int(arguments[0])
    if run_id < 1:
        raise CommandUsageError("workflow run ID must be positive")
    return ParsedCommand(name, run_id)


def command_event(event: dict[str, Any], repository: str) -> CommandEvent | None:
    """Authorize and bind one command to a PR before any Actions metadata read."""
    if not isinstance(event, dict) or event.get("action") != "created":
        return None
    event_repository = event.get("repository", {})
    if not isinstance(event_repository, dict) or event_repository.get("full_name") != repository:
        return None
    issue = event.get("issue", {})
    comment = event.get("comment", {})
    if not isinstance(issue, dict) or not isinstance(comment, dict) or not issue.get("pull_request"):
        return None
    if str(comment.get("author_association", "")).upper() not in TRUSTED_ASSOCIATIONS:
        return None
    command = parse_comment(comment.get("body", ""))
    if command is None:
        return None
    issue_number = issue.get("number")
    comment_id = comment.get("id")
    if not isinstance(issue_number, int) or issue_number < 1:
        raise CommandUsageError("event has no valid pull request number")
    if not isinstance(comment_id, int) or comment_id < 1:
        raise CommandUsageError("event has no valid comment ID")
    return CommandEvent(repository, issue_number, comment_id, "", command)


def _with_head_sha(command: CommandEvent, pull_request: dict[str, Any]) -> CommandEvent:
    head = pull_request.get("head") if isinstance(pull_request, dict) else None
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(head_sha, str) or not _HEAD_SHA.fullmatch(head_sha):
        raise GitHubAPIError("validate pull request head", None)
    return CommandEvent(
        repository=command.repository,
        issue_number=command.issue_number,
        comment_id=command.comment_id,
        head_sha=head_sha.lower(),
        command=command.command,
    )


def _markdown(value: Any, maximum: int = 100) -> str:
    text = " ".join(str(value if value is not None else "").split())[:maximum]
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("\\", "\\\\").replace("`", "'").replace("@", "@\u200b")
    for character in "|*[]()_#!~":
        text = text.replace(character, "\\" + character)
    return text or "—"


def _state(value: Any) -> str:
    normalized = str(value or "unknown").casefold()
    icon = {
        "success": "✅",
        "failure": "❌",
        "cancelled": "⛔",
        "timed_out": "⏱️",
        "startup_failure": "❌",
        "action_required": "⚠️",
        "stale": "⚠️",
        "in_progress": "🔄",
        "queued": "⏳",
        "waiting": "⏳",
        "pending": "⏳",
        "skipped": "⚪",
        "neutral": "⚪",
    }.get(normalized, "❔")
    return f"{icon} {_markdown(normalized, 30)}"


def _result(item: dict[str, Any]) -> str:
    return str(item.get("conclusion") or item.get("status") or "unknown").casefold()


def _run_id(run: dict[str, Any]) -> int | None:
    value = run.get("id")
    return value if isinstance(value, int) and value > 0 else None


def _run_link(repository: str, run_id: int) -> str:
    return f"https://github.com/{repository}/actions/runs/{run_id}"


def _job_link(repository: str, run_id: int, job: dict[str, Any]) -> str:
    job_id = job.get("id")
    if isinstance(job_id, int) and job_id > 0:
        return f"{_run_link(repository, run_id)}/job/{job_id}"
    return _run_link(repository, run_id)


def _failed_steps(job: dict[str, Any]) -> list[str]:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    names = [
        _markdown(step.get("name", "unnamed step"), 80)
        for step in steps
        if isinstance(step, dict) and str(step.get("conclusion", "")).casefold() in _TERMINAL_FAILURES
    ]
    return names[:MAX_FAILED_STEPS]


def _runs_for_head(api: GitHubAPI, head_sha: str) -> list[dict[str, Any]]:
    return [run for run in api.workflow_runs(head_sha) if str(run.get("head_sha", "")).lower() == head_sha]


def _run_table(repository: str, runs: list[dict[str, Any]]) -> list[str]:
    lines = ["| Run | Workflow | Event | State | Attempt |", "|---:|---|---|---|---:|"]
    for run in runs:
        run_id = _run_id(run)
        if run_id is None:
            continue
        lines.append(
            f"| [#{run_id}]({_run_link(repository, run_id)}) | {_markdown(run.get('name'))} | "
            f"{_markdown(run.get('event'), 30)} | {_state(_result(run))} | {_markdown(run.get('run_attempt', 1), 10)} |"
        )
    return lines


def _job_table(repository: str, run_id: int, jobs: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Job | State | Runner | Group | Labels | Failed step(s) |",
        "|---|---|---|---|---|---|",
    ]
    for job in jobs:
        runner = job.get("runner_name") or (
            "unassigned" if _result(job) in {"queued", "waiting", "pending"} else "not reported"
        )
        labels = job.get("labels")
        label_text = ", ".join(_markdown(label, 40) for label in labels[:8]) if isinstance(labels, list) else "—"
        failed = ", ".join(_failed_steps(job)) or "—"
        name = _markdown(job.get("name"), 100)
        link = _job_link(repository, run_id, job)
        lines.append(
            f"| [{name}]({link}) | {_state(_result(job))} | {_markdown(runner, 80)} | "
            f"{_markdown(job.get('runner_group_name') or 'not reported', 60)} | {label_text} | {failed} |"
        )
    if len(jobs) >= MAX_JOBS:
        lines.append(
            f"| — | ⚠️ latest-attempt view is capped at {MAX_JOBS} jobs; open the run for the complete matrix. "
            "| — | — | — | — |"
        )
    return lines


def _help() -> str:
    return "\n".join(
        [
            "## VibeCI read-only CI diagnostics",
            "",
            "Commands are case-insensitive and may use the `/vibeci` prefix:",
            "",
            "| Command | Result |",
            "|---|---|",
            "| `/help` | Show this contract. |",
            "| `/ci` | Show current-head runs, jobs, runner assignments and failed steps. |",
            "| `/runs` | List up to five runs for the PR's exact current head SHA. |",
            "| `/run <id>` | Show one current-head run and its jobs. |",
            "| `/runners` | Show runners assigned to current-head jobs. |",
            "| `/failures` | Show failed/cancelled/timed-out jobs and failed steps. |",
            "",
            "Examples: `/Help`, `/vibeci ci`, `/RUN 40240597994`.",
            "",
            "This surface is diagnostics-only: it cannot run, rerun, cancel, approve, merge, or execute PR code. "
            "It reports runner assignments from workflow jobs, not an admin-level self-hosted-runner inventory.",
            f"Each run view is capped at {MAX_JOBS} latest-attempt jobs; use the linked run for a larger matrix.",
        ]
    )


def _render_runs(repository: str, head_sha: str, runs: list[dict[str, Any]]) -> str:
    lines = ["## VibeCI runs", "", f"Exact PR head: `{head_sha}`", ""]
    if not runs:
        lines.append("No GitHub Actions workflow runs were found for this exact head commit.")
    else:
        lines.extend(_run_table(repository, runs))
    return "\n".join(lines)


def _render_one_run(api: GitHubAPI, event: CommandEvent, run: dict[str, Any]) -> str:
    run_id = _run_id(run)
    if run_id is None:
        raise GitHubAPIError("validate workflow run", None)
    if str(run.get("head_sha", "")).lower() != event.head_sha:
        raise CommandUsageError("requested run does not belong to this pull request's current head SHA")
    jobs = api.workflow_jobs(run_id)
    lines = [
        "## VibeCI workflow run",
        "",
        f"- Run: [#{run_id}]({_run_link(event.repository, run_id)})",
        f"- Workflow: **{_markdown(run.get('name'))}**",
        f"- Head: `{event.head_sha}`",
        f"- Event: `{_markdown(run.get('event'), 30)}`",
        f"- State: {_state(_result(run))}",
        f"- Attempt: `{_markdown(run.get('run_attempt', 1), 10)}`",
        "",
    ]
    lines.extend(_job_table(event.repository, run_id, jobs))
    return "\n".join(lines)


def _render_ci(api: GitHubAPI, event: CommandEvent, runs: list[dict[str, Any]]) -> str:
    lines = ["## VibeCI current CI", "", f"Exact PR head: `{event.head_sha}`", ""]
    if not runs:
        return "\n".join([*lines, "No GitHub Actions workflow runs were found for this exact head commit."])
    lines.extend(_run_table(event.repository, runs))
    for run in runs:
        run_id = _run_id(run)
        if run_id is None:
            continue
        jobs = api.workflow_jobs(run_id)
        lines.extend(["", f"### Run #{run_id}: {_markdown(run.get('name'))}", ""])
        lines.extend(_job_table(event.repository, run_id, jobs))
    return "\n".join(lines)


def _render_runners(api: GitHubAPI, event: CommandEvent, runs: list[dict[str, Any]]) -> str:
    lines = [
        "## VibeCI assigned runners",
        "",
        f"Exact PR head: `{event.head_sha}`",
        "",
        "This is job-assignment evidence, not an admin-level self-hosted-runner inventory.",
        "",
        "| Run | Workflow / job | State | Runner | Group | Labels |",
        "|---:|---|---|---|---|---|",
    ]
    count = 0
    capped = False
    for run in runs:
        run_id = _run_id(run)
        if run_id is None:
            continue
        jobs = api.workflow_jobs(run_id)
        capped = capped or len(jobs) >= MAX_JOBS
        for job in jobs:
            runner = job.get("runner_name") or "unassigned"
            labels = job.get("labels")
            label_text = ", ".join(_markdown(label, 40) for label in labels[:8]) if isinstance(labels, list) else "—"
            lines.append(
                f"| [#{run_id}]({_run_link(event.repository, run_id)}) | {_markdown(run.get('name'), 60)} / "
                f"{_markdown(job.get('name'), 80)} | {_state(_result(job))} | {_markdown(runner, 80)} | "
                f"{_markdown(job.get('runner_group_name') or 'not reported', 60)} | {label_text} |"
            )
            count += 1
    if count == 0:
        lines.append("| — | No jobs have an assigned runner yet. | — | — | — | — |")
    if capped:
        lines.extend(["", f"⚠️ Each run's latest-attempt view is capped at {MAX_JOBS} jobs."])
    return "\n".join(lines)


def _render_failures(api: GitHubAPI, event: CommandEvent, runs: list[dict[str, Any]]) -> str:
    lines = ["## VibeCI failures", "", f"Exact PR head: `{event.head_sha}`", ""]
    failed: list[tuple[int, dict[str, Any]]] = []
    capped = False
    for run in runs:
        run_id = _run_id(run)
        if run_id is None:
            continue
        jobs = api.workflow_jobs(run_id)
        capped = capped or len(jobs) >= MAX_JOBS
        failed.extend((run_id, job) for job in jobs if _result(job) in _TERMINAL_FAILURES)
    if not failed:
        qualifier = f" among the first {MAX_JOBS} latest-attempt jobs per run" if capped else ""
        lines.append(
            "No failed, cancelled, timed-out, stale, or action-required jobs were found"
            f"{qualifier} for current-head runs."
        )
        return "\n".join(lines)
    lines.extend(["| Run | Job | State | Failed step(s) |", "|---:|---|---|---|"])
    for run_id, job in failed:
        failed_steps = ", ".join(_failed_steps(job)) or "No failed step name was reported; open the job link."
        lines.append(
            f"| [#{run_id}]({_run_link(event.repository, run_id)}) | "
            f"[{_markdown(job.get('name'), 100)}]({_job_link(event.repository, run_id, job)}) | "
            f"{_state(_result(job))} | {failed_steps} |"
        )
    lines.extend(["", "Job logs are intentionally not copied into comments; use the linked GitHub job view."])
    if capped:
        lines.append(f"⚠️ Each run's latest-attempt view is capped at {MAX_JOBS} jobs.")
    return "\n".join(lines)


def render_command(api: GitHubAPI, event: CommandEvent) -> str:
    """Fetch only data needed by the selected command and render bounded Markdown."""
    if event.command.name == "help":
        body = _help()
    elif event.command.name == "run":
        if event.command.run_id is None:
            raise CommandUsageError("usage: /run <numeric-run-id>")
        body = _render_one_run(api, event, api.workflow_run(event.command.run_id))
    else:
        runs = _runs_for_head(api, event.head_sha)
        if event.command.name == "runs":
            body = _render_runs(event.repository, event.head_sha, runs)
        elif event.command.name == "ci":
            body = _render_ci(api, event, runs)
        elif event.command.name == "runners":
            body = _render_runners(api, event, runs)
        elif event.command.name == "failures":
            body = _render_failures(api, event, runs)
        else:  # Defensive: parser and dispatch must evolve together.
            raise CommandUsageError(f"unsupported command: {event.command.name}")
    return _bounded_response(body, event.comment_id)


def _bounded_response(body: str, comment_id: int) -> str:
    footer = f"\n\n_Requested by comment `{comment_id}`._"
    complete = f"{_RESPONSE_MARKER}\n{body}{footer}"
    if len(complete) <= MAX_COMMENT_CHARS:
        return complete
    notice = "\n\n> Output truncated by BetterCI's comment budget. Use `/run <id>` for one bounded run."
    available = MAX_COMMENT_CHARS - len(_RESPONSE_MARKER) - len(notice) - len(footer) - 2
    clipped = body[:available]
    if "\n" in clipped:
        clipped = clipped.rsplit("\n", 1)[0]
    return f"{_RESPONSE_MARKER}\n{clipped}{notice}{footer}"


def _usage_error_body(message: str, comment_id: int) -> str:
    safe = _markdown(message, 500)
    return _bounded_response(
        "\n".join(
            [
                "## VibeCI diagnostic command rejected",
                "",
                safe,
                "",
                "Use `/help` for the case-insensitive read-only command grammar.",
                "No workflow was rerun or cancelled, and no PR code was executed.",
            ]
        ),
        comment_id,
    )


def _error_body(message: str, comment_id: int) -> str:
    safe = _markdown(message, 500)
    return "\n".join(
        [
            _RESPONSE_MARKER,
            "## VibeCI diagnostic command failed",
            "",
            safe,
            "",
            f"Review the least-privilege token settings: {_PERMISSION_HELP}",
            "",
            "No workflow was rerun or cancelled, and no PR code was executed.",
            "",
            f"_Requested by comment `{comment_id}`._",
        ]
    )


def _annotation(message: str) -> None:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title=VibeCI read-only command::{escaped}")


def _event_comment_ids(event: dict[str, Any]) -> tuple[int, int] | None:
    issue = event.get("issue") if isinstance(event, dict) else None
    comment = event.get("comment") if isinstance(event, dict) else None
    issue_number = issue.get("number") if isinstance(issue, dict) else None
    comment_id = comment.get("id") if isinstance(comment, dict) else None
    if not isinstance(issue_number, int) or issue_number < 1:
        return None
    if not isinstance(comment_id, int) or comment_id < 1:
        return None
    return issue_number, comment_id


def run(event: dict[str, Any], repository: str, api: GitHubAPI) -> int:
    """Handle one event. Irrelevant or unauthorized comments are silent no-ops."""
    try:
        command = command_event(event, repository)
    except CommandUsageError as exc:
        coordinates = _event_comment_ids(event)
        if coordinates is not None:
            try:
                api.post_comment(coordinates[0], _usage_error_body(str(exc), coordinates[1]))
            except GitHubAPIError as post_exc:
                _annotation(str(post_exc))
        _annotation(str(exc))
        return 2
    if command is None:
        return 0
    try:
        command = _with_head_sha(command, api.pull_request(command.issue_number))
        response = render_command(api, command)
    except CommandUsageError as exc:
        try:
            api.post_comment(command.issue_number, _usage_error_body(str(exc), command.comment_id))
        except GitHubAPIError as post_exc:
            _annotation(str(post_exc))
        _annotation(str(exc))
        return 2
    except GitHubAPIError as exc:
        try:
            api.post_comment(command.issue_number, _error_body(str(exc), command.comment_id))
        except GitHubAPIError as post_exc:
            _annotation(str(post_exc))
        _annotation(str(exc))
        return 2
    try:
        api.post_comment(command.issue_number, response)
    except GitHubAPIError as exc:
        _annotation(str(exc))
        return 2
    return 0


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        if not event_path:
            raise ValueError("GITHUB_EVENT_PATH is required")
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
        api = GitHubAPI(repository, token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
        return run(event, repository, api)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _annotation(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
