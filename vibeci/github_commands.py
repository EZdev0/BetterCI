"""Read-only GitHub issue and PR commands for CI and runner diagnostics.

The module intentionally uses only the Python standard library so the
``issue_comment`` workflow can execute reviewed default-branch code without
installing dependencies or restoring a cache. Comment text and authorization
policy are parsed as data; neither becomes a shell command, checkout ref, URL,
or unrestricted API path.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .github_api import (
    MAX_CANDIDATE_RUNS,
    MAX_COMMENT_CHARS,
    MAX_JOBS,
    MAX_RESPONSE_BYTES,
    MAX_RUNS,
    REQUEST_TIMEOUT_SECONDS,
    GitHubAPI,
    GitHubAPIError,
)
from .github_policy import (
    MAX_POLICY_CHARS,
    CommandEvent,
    CommandPolicy,
    CommandPolicyError,
    CommandUsageError,
    command_event,
    parse_comment,
    parse_policy,
    resolve_target,
    validate_runtime_context,
)

__all__ = [
    "MAX_COMMENT_CHARS",
    "MAX_POLICY_CHARS",
    "CommandPolicy",
    "CommandPolicyError",
    "CommandUsageError",
    "GitHubAPI",
    "main",
    "parse_comment",
    "parse_policy",
    "run",
]

MAX_FAILED_STEPS = 5
_TERMINAL_FAILURES = frozenset(
    {"failure", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
)
_COMMAND_WORKFLOW_PATH = ".github/workflows/vibeci-commands.yml"
_RESPONSE_MARKER = "<!-- vibeci-read-only-ci-diagnostics -->"
_PERMISSION_HELP = (
    "https://docs.github.com/en/actions/security-for-github-actions/security-guides/"
    "automatic-token-authentication#modifying-the-permissions-for-the-github_token"
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


def _is_command_workflow_run(run: dict[str, Any]) -> bool:
    path = str(run.get("path", "")).split("@", 1)[0]
    return path == _COMMAND_WORKFLOW_PATH


def _runs_for_head(api: GitHubAPI, head_sha: str) -> tuple[list[dict[str, Any]], bool]:
    candidates = api.workflow_runs(head_sha)
    runs = [
        run
        for run in candidates
        if str(run.get("head_sha", "")).lower() == head_sha and not _is_command_workflow_run(run)
    ]
    return runs[:MAX_RUNS], len(candidates) >= MAX_CANDIDATE_RUNS


def _target_line(event: CommandEvent) -> str:
    return f"Exact target ({event.target_label}): `{event.head_sha}`"


def _candidate_notice(candidate_limit_reached: bool) -> list[str]:
    if not candidate_limit_reached:
        return []
    return [
        "",
        f"⚠️ The scan reached its {MAX_CANDIDATE_RUNS}-candidate bound before command-workflow filtering.",
    ]


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
            "| `/ci [--target default\\|pr:<number>]` | Show runs, jobs, runners and failed steps. |",
            "| `/runs [--target default\\|pr:<number>]` | List up to five runs for the exact target SHA. |",
            "| `/run <id> [--target default\\|pr:<number>]` | Show one target-bound run and its jobs. |",
            "| `/runners [--target default\\|pr:<number>]` | Show runners assigned to target-bound jobs. |",
            "| `/failures [--target default\\|pr:<number>]` | Show failed jobs and failed steps. |",
            "",
            "Without `--target`, a PR conversation selects that PR's current head; an allowed control issue "
            "selects the exact default-branch event head.",
            "Examples: `/Help`, `/vibeci ci --target default`, `/runs --target pr:5`.",
            "",
            "Access requires both a trusted repository role and an exact login/user-ID policy match; ordinary "
            "issues must also be policy-listed control issues.",
            "This surface is diagnostics-only: it cannot run, rerun, cancel, approve, merge, or execute PR code. "
            "It reports runner assignments from workflow jobs, not an admin-level self-hosted-runner inventory.",
            f"At most {MAX_CANDIDATE_RUNS} candidate runs are scanned and {MAX_RUNS} non-command runs reported.",
            f"Each run view is capped at {MAX_JOBS} latest-attempt jobs; use the linked run for a larger matrix.",
            f"Each REST response is capped at {MAX_RESPONSE_BYTES // (1024 * 1024)} MiB with a "
            f"{REQUEST_TIMEOUT_SECONDS}-second timeout.",
        ]
    )


def _render_runs(event: CommandEvent, runs: list[dict[str, Any]], candidate_limit_reached: bool) -> str:
    lines = ["## VibeCI runs", "", _target_line(event), ""]
    if not runs:
        lines.append("No eligible GitHub Actions workflow runs were found for this exact target commit.")
    else:
        lines.extend(_run_table(event.repository, runs))
    lines.extend(_candidate_notice(candidate_limit_reached))
    return "\n".join(lines)


def _render_one_run(api: GitHubAPI, event: CommandEvent, run: dict[str, Any]) -> str:
    run_id = _run_id(run)
    if run_id is None:
        raise GitHubAPIError("validate workflow run", None)
    if _is_command_workflow_run(run):
        raise CommandUsageError("requested run is the read-only command adapter itself")
    if str(run.get("head_sha", "")).lower() != event.head_sha:
        raise CommandUsageError("requested run does not belong to the selected target's exact head SHA")
    jobs = api.workflow_jobs(run_id)
    lines = [
        "## VibeCI workflow run",
        "",
        f"- Run: [#{run_id}]({_run_link(event.repository, run_id)})",
        f"- Workflow: **{_markdown(run.get('name'))}**",
        f"- Target: {_markdown(event.target_label, 80)}",
        f"- Head: `{event.head_sha}`",
        f"- Event: `{_markdown(run.get('event'), 30)}`",
        f"- State: {_state(_result(run))}",
        f"- Attempt: `{_markdown(run.get('run_attempt', 1), 10)}`",
        "",
    ]
    lines.extend(_job_table(event.repository, run_id, jobs))
    return "\n".join(lines)


def _render_ci(
    api: GitHubAPI,
    event: CommandEvent,
    runs: list[dict[str, Any]],
    candidate_limit_reached: bool,
) -> str:
    lines = ["## VibeCI current CI", "", _target_line(event), ""]
    if not runs:
        lines.append("No eligible GitHub Actions workflow runs were found for this exact target commit.")
    else:
        lines.extend(_run_table(event.repository, runs))
        for run in runs:
            run_id = _run_id(run)
            if run_id is None:
                continue
            jobs = api.workflow_jobs(run_id)
            lines.extend(["", f"### Run #{run_id}: {_markdown(run.get('name'))}", ""])
            lines.extend(_job_table(event.repository, run_id, jobs))
    lines.extend(_candidate_notice(candidate_limit_reached))
    return "\n".join(lines)


def _render_runners(
    api: GitHubAPI,
    event: CommandEvent,
    runs: list[dict[str, Any]],
    candidate_limit_reached: bool,
) -> str:
    lines = [
        "## VibeCI assigned runners",
        "",
        _target_line(event),
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
    lines.extend(_candidate_notice(candidate_limit_reached))
    return "\n".join(lines)


def _render_failures(
    api: GitHubAPI,
    event: CommandEvent,
    runs: list[dict[str, Any]],
    candidate_limit_reached: bool,
) -> str:
    lines = ["## VibeCI failures", "", _target_line(event), ""]
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
            f"{qualifier} for target-bound runs."
        )
    else:
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
    lines.extend(_candidate_notice(candidate_limit_reached))
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
        runs, candidate_limit_reached = _runs_for_head(api, event.head_sha)
        if event.command.name == "runs":
            body = _render_runs(event, runs, candidate_limit_reached)
        elif event.command.name == "ci":
            body = _render_ci(api, event, runs, candidate_limit_reached)
        elif event.command.name == "runners":
            body = _render_runners(api, event, runs, candidate_limit_reached)
        elif event.command.name == "failures":
            body = _render_failures(api, event, runs, candidate_limit_reached)
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


def run(
    event: dict[str, Any],
    repository: str,
    api: GitHubAPI,
    policy: CommandPolicy,
    actor: str,
    default_sha: str,
) -> int:
    """Handle one event. Irrelevant or unauthorized comments are silent no-ops."""
    try:
        command = command_event(event, repository, policy, actor, default_sha)
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
        if command.command.name != "help":
            command = resolve_target(command, api)
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
    actor = os.environ.get("VIBECI_ACTOR", "")
    default_sha = os.environ.get("VIBECI_DEFAULT_SHA", "")
    try:
        if not event_path:
            raise ValueError("GITHUB_EVENT_PATH is required")
        validate_runtime_context(actor, default_sha)
        policy = parse_policy(os.environ.get("VIBECI_COMMAND_POLICY_JSON", ""))
        with open(event_path, encoding="utf-8") as handle:
            event = json.load(handle)
        api = GitHubAPI(repository, token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
        return run(event, repository, api, policy, actor, default_sha)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _annotation(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
