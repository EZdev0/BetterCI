"""Fail-closed policy, grammar, and target binding for GitHub commands."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from .github_api import GitHubAPI, GitHubAPIError

MAX_COMMAND_CHARS = 200
MAX_POLICY_CHARS = 8_192
MAX_POLICY_ACTORS = 50
MAX_CONTROL_ISSUES = 50
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
_COMMAND_NAMES = frozenset({"help", "ci", "runs", "run", "runners", "failures"})
_BARE_COMMANDS = frozenset(f"/{name}" for name in _COMMAND_NAMES)
_HEAD_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


class CommandUsageError(ValueError):
    """The comment selected this adapter but did not match its bounded grammar."""


class CommandPolicyError(ValueError):
    """The secret-backed command policy is missing or fails closed validation."""


@dataclass(frozen=True)
class CommandPolicy:
    trusted_actors: frozenset[tuple[str, int]]
    control_issues: frozenset[int]


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    run_id: int | None = None
    target: str = "auto"
    target_pull_request: int | None = None


@dataclass(frozen=True)
class CommandEvent:
    repository: str
    issue_number: int
    comment_id: int
    pull_request_number: int | None
    default_sha: str
    head_sha: str
    target_label: str
    command: ParsedCommand


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def valid_login(value: str) -> bool:
    return bool(_LOGIN.fullmatch(value))


def valid_head_sha(value: str) -> bool:
    return bool(_HEAD_SHA.fullmatch(value))


def validate_runtime_context(actor: str, default_sha: str) -> None:
    if not valid_login(actor):
        raise ValueError("VIBECI_ACTOR must be a valid GitHub login")
    if not valid_head_sha(default_sha):
        raise ValueError("VIBECI_DEFAULT_SHA must be a full GitHub commit SHA")


def _load_policy(raw: str) -> dict[str, Any]:
    if not raw:
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON is required")
    if len(raw) > MAX_POLICY_CHARS:
        raise CommandPolicyError(f"VIBECI_COMMAND_POLICY_JSON exceeds {MAX_POLICY_CHARS} characters")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON is not valid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"version", "trusted_actors", "control_issues"}:
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has an invalid object shape")
    if not _positive_int(value["version"]) or value["version"] != 1:
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has an unsupported version")
    return value


def _policy_actor(value: Any) -> tuple[str, int]:
    if not isinstance(value, dict) or set(value) != {"login", "id"}:
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has an invalid trusted actor")
    login = value["login"]
    user_id = value["id"]
    if not isinstance(login, str) or not valid_login(login) or not _positive_int(user_id):
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has an invalid trusted actor")
    return login.casefold(), user_id


def _trusted_actors(value: Any) -> frozenset[tuple[str, int]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_POLICY_ACTORS:
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has an invalid trusted_actors list")
    actors = frozenset(_policy_actor(actor) for actor in value)
    if len(actors) != len(value):
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has duplicate trusted actors")
    return actors


def _control_issues(value: Any) -> frozenset[int]:
    if not isinstance(value, list) or len(value) > MAX_CONTROL_ISSUES:
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has an invalid control_issues list")
    if not all(_positive_int(issue_number) for issue_number in value):
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has an invalid control issue")
    issues = frozenset(value)
    if len(issues) != len(value):
        raise CommandPolicyError("VIBECI_COMMAND_POLICY_JSON has duplicate control issues")
    return issues


def parse_policy(raw: str) -> CommandPolicy:
    """Parse the bounded secret policy without returning or logging its content."""
    value = _load_policy(raw)
    return CommandPolicy(
        trusted_actors=_trusted_actors(value["trusted_actors"]),
        control_issues=_control_issues(value["control_issues"]),
    )


def _targets_adapter(line: str) -> bool:
    parts = line.split(maxsplit=1)
    first = parts[0].casefold() if parts else ""
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


def _parse_target(arguments: list[str], command_name: str) -> tuple[str, int | None]:
    if not arguments:
        return "auto", None
    usage = f"usage: /{command_name} [--target default|pr:<number>]"
    if len(arguments) != 2 or arguments[0].casefold() != "--target":
        raise CommandUsageError(usage)
    selector = arguments[1].casefold()
    if selector == "default":
        return "default", None
    if selector.startswith("pr:"):
        number = selector.removeprefix("pr:")
        if number.isascii() and number.isdigit() and int(number) > 0:
            return "pull_request", int(number)
    raise CommandUsageError(usage)


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
    if name == "help":
        if arguments:
            raise CommandUsageError("/help takes no arguments")
        return ParsedCommand(name)
    if name != "run":
        target, target_pull_request = _parse_target(arguments, name)
        return ParsedCommand(name, target=target, target_pull_request=target_pull_request)
    if not arguments or not arguments[0].isascii() or not arguments[0].isdigit():
        raise CommandUsageError("usage: /run <numeric-run-id> [--target default|pr:<number>]")
    run_id = int(arguments[0])
    if run_id < 1:
        raise CommandUsageError("workflow run ID must be positive")
    target, target_pull_request = _parse_target(arguments[1:], "run <numeric-run-id>")
    return ParsedCommand(name, run_id, target, target_pull_request)


def _event_maps(event: dict[str, Any], repository: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not isinstance(event, dict) or event.get("action") != "created":
        return None
    event_repository = event.get("repository", {})
    if not isinstance(event_repository, dict) or event_repository.get("full_name") != repository:
        return None
    issue = event.get("issue", {})
    comment = event.get("comment", {})
    if not isinstance(issue, dict) or not isinstance(comment, dict):
        return None
    return issue, comment


def _event_coordinates(issue: dict[str, Any], comment: dict[str, Any]) -> tuple[int, int]:
    issue_number = issue.get("number")
    comment_id = comment.get("id")
    if not _positive_int(issue_number):
        raise CommandUsageError("event has no valid issue or pull request number")
    if not _positive_int(comment_id):
        raise CommandUsageError("event has no valid comment ID")
    return issue_number, comment_id


def _authorized_actor(comment: dict[str, Any], policy: CommandPolicy, actor: str) -> bool:
    if str(comment.get("author_association", "")).upper() not in TRUSTED_ASSOCIATIONS:
        return False
    user = comment.get("user", {})
    login = user.get("login") if isinstance(user, dict) else None
    user_id = user.get("id") if isinstance(user, dict) else None
    return bool(
        isinstance(login, str)
        and valid_login(login)
        and _positive_int(user_id)
        and actor.casefold() == login.casefold()
        and (login.casefold(), user_id) in policy.trusted_actors
    )


def command_event(
    event: dict[str, Any],
    repository: str,
    policy: CommandPolicy,
    actor: str,
    default_sha: str,
) -> CommandEvent | None:
    """Authorize one issue/PR command before any Actions metadata read."""
    maps = _event_maps(event, repository)
    if maps is None:
        return None
    issue, comment = maps
    if not _authorized_actor(comment, policy, actor):
        return None
    issue_number, comment_id = _event_coordinates(issue, comment)
    pull_request_number = issue_number if isinstance(issue.get("pull_request"), dict) else None
    if pull_request_number is None and issue_number not in policy.control_issues:
        return None
    command = parse_comment(comment.get("body", ""))
    if command is None:
        return None
    return CommandEvent(
        repository=repository,
        issue_number=issue_number,
        comment_id=comment_id,
        pull_request_number=pull_request_number,
        default_sha=default_sha,
        head_sha="",
        target_label="",
        command=command,
    )


def _pull_request_target(command: CommandEvent, api: GitHubAPI, number: int) -> CommandEvent:
    pull_request = api.pull_request(number)
    head = pull_request.get("head") if isinstance(pull_request, dict) else None
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(head_sha, str) or not valid_head_sha(head_sha):
        raise GitHubAPIError("validate pull request head", None)
    return replace(command, head_sha=head_sha.lower(), target_label=f"PR #{number} head")


def resolve_target(command: CommandEvent, api: GitHubAPI) -> CommandEvent:
    """Resolve only the event default SHA or a same-repository pull request head."""
    target = command.command.target
    if target == "auto":
        target = "pull_request" if command.pull_request_number is not None else "default"
    if target == "default":
        if not valid_head_sha(command.default_sha):
            raise GitHubAPIError("validate default branch event head", None)
        return replace(
            command,
            head_sha=command.default_sha.lower(),
            target_label="default branch event head",
        )
    number = command.command.target_pull_request or command.pull_request_number
    if target != "pull_request" or not _positive_int(number):
        raise CommandUsageError("selected target has no valid pull request number")
    return _pull_request_target(command, api, number)
