"""Strict loading and repository-path scope for committed agent settings."""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

_REQUIRED_TOP_LEVEL = {
    "version",
    "repository_language",
    "default_mode",
    "state",
    "goal",
    "approval",
    "scope",
    "changes",
    "agents",
    "orchestration",
    "context",
    "notifications",
    "research",
    "verification",
    "providers",
    "schedules",
}


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"agent settings {key} must be a mapping")
    return value


def _exact_keys(mapping: dict[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(mapping)
    extra = set(mapping) - expected
    if missing or extra:
        raise ValueError(f"{label} keys mismatch; missing={sorted(missing)}, extra={sorted(extra)}")


def _string_list(mapping: dict[str, Any], key: str, *, allow_empty: bool = False) -> list[str]:
    value = mapping.get(key)
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"agent settings {key} must be a{' possibly empty' if allow_empty else ' non-empty'} list")
    if any(not isinstance(item, str) or not item or len(item) > 300 or "\x00" in item for item in value):
        raise ValueError(f"agent settings {key} contains an invalid string")
    return value


def _bounded_integer(mapping: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"agent settings {key} must be an integer in {minimum}-{maximum}")
    return value


def _boolean(mapping: dict[str, Any], key: str) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"agent settings {key} must be boolean")
    return value


def _relative_path(value: Any, label: str, *, allow_git: bool = False) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} must be a repository-relative POSIX path")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or (path.parts[0] == ".git" and not allow_git):
        raise ValueError(f"{label} must be a repository-relative POSIX path")
    return path


def _validate_scope(raw: dict[str, Any]) -> None:
    scope = _mapping(raw, "scope")
    _exact_keys(
        scope,
        {"ignored_directories", "ignored_extensions", "allowed_path_globs", "denied_path_globs"},
        "scope settings",
    )
    ignored_directories = _string_list(scope, "ignored_directories", allow_empty=True)
    if any(
        not PurePosixPath(directory).parts
        or PurePosixPath(directory).is_absolute()
        or ".." in PurePosixPath(directory).parts
        or "\\" in directory
        for directory in ignored_directories
    ):
        raise ValueError("ignored_directories entries must be repository-relative POSIX paths")
    extensions = _string_list(scope, "ignored_extensions", allow_empty=True)
    if any(not extension.startswith(".") or "/" in extension or "\\" in extension for extension in extensions):
        raise ValueError("ignored_extensions entries must be dot-prefixed extensions")
    for key in ("allowed_path_globs", "denied_path_globs"):
        patterns = _string_list(scope, key, allow_empty=key == "denied_path_globs")
        if any(
            "\\" in pattern or pattern.startswith("/") or ".." in PurePosixPath(pattern).parts for pattern in patterns
        ):
            raise ValueError(f"{key} entries must be repository-relative POSIX globs")


def _validate_state_goal_approval(raw: dict[str, Any]) -> None:
    state = _mapping(raw, "state")
    _exact_keys(state, {"directory", "file", "worktrees", "branch_prefix"}, "state settings")
    directory = _relative_path(state.get("directory"), "state.directory")
    state_file = _relative_path(state.get("file"), "state.file")
    worktrees = _relative_path(state.get("worktrees"), "state.worktrees")
    if (
        tuple(state_file.parts[: len(directory.parts)]) != directory.parts
        or tuple(worktrees.parts[: len(directory.parts)]) != directory.parts
    ):
        raise ValueError("state file and worktrees must stay below state.directory")
    prefix = state.get("branch_prefix")
    if (
        not isinstance(prefix, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,100}", prefix)
        or ".." in prefix
        or "/" not in prefix
        or not prefix.endswith(("/", "-"))
    ):
        raise ValueError("state.branch_prefix must be a bounded Git namespace ending in '/' or '-'")

    goal = _mapping(raw, "goal")
    _exact_keys(
        goal,
        {
            "required",
            "minimum_acceptance_criteria",
            "minimum_references",
            "maximum_objective_chars",
            "maximum_criterion_chars",
        },
        "goal settings",
    )
    if _boolean(goal, "required") is not True:
        raise ValueError("goal.required cannot be disabled")
    _bounded_integer(goal, "minimum_acceptance_criteria", 1, 20)
    _bounded_integer(goal, "minimum_references", 1, 20)
    _bounded_integer(goal, "maximum_objective_chars", 5, 500)
    _bounded_integer(goal, "maximum_criterion_chars", 5, 300)

    approval = _mapping(raw, "approval")
    _exact_keys(approval, {"required_before_writes", "commands", "plan_digest"}, "approval settings")
    if _boolean(approval, "required_before_writes") is not True or approval.get("plan_digest") != "sha256":
        raise ValueError("approval-before-write and SHA-256 plan digests cannot be disabled")
    if set(_string_list(approval, "commands")) != {"/approve", "/ok"}:
        raise ValueError("approval commands must be /approve and /ok")


def _validate_change_limits(raw: dict[str, Any]) -> None:
    changes = _mapping(raw, "changes")
    _exact_keys(
        changes,
        {
            "forbid_file_deletion",
            "forbid_renames",
            "maximum_deleted_lines_per_file",
            "maximum_deletion_ratio",
            "maximum_changed_files",
            "maximum_added_file_bytes",
            "require_planned_paths",
            "require_english_documentation",
            "protected_paths",
            "report_only_risk_patterns",
        },
        "change settings",
    )
    for key in (
        "forbid_file_deletion",
        "forbid_renames",
        "require_planned_paths",
        "require_english_documentation",
    ):
        if _boolean(changes, key) is not True:
            raise ValueError(f"changes.{key} cannot be disabled")
    _bounded_integer(changes, "maximum_deleted_lines_per_file", 0, 10_000)
    ratio = changes.get("maximum_deletion_ratio")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 <= ratio <= 1:
        raise ValueError("maximum_deletion_ratio must be a number in 0-1")
    _bounded_integer(changes, "maximum_changed_files", 1, 500)
    _bounded_integer(changes, "maximum_added_file_bytes", 1_000, 10_000_000)
    for protected in _string_list(changes, "protected_paths"):
        _relative_path(protected, "changes.protected_paths entry", allow_git=True)
    categories = set(_string_list(changes, "report_only_risk_patterns"))
    expected_categories = {
        "curl ... | shell",
        "wget ... | shell",
        "dynamic eval/exec",
        "credential or token exfiltration",
        "recursive destructive delete",
        "world-writable permissions",
        "workflow secret exposure",
    }
    if categories != expected_categories:
        raise ValueError("report_only_risk_patterns must mirror the reviewed detector categories")


def _validate_orchestration(raw: dict[str, Any]) -> None:
    orchestration = _mapping(raw, "orchestration")
    _exact_keys(
        orchestration,
        {
            "teams_file",
            "knowledge_directory",
            "require_manager",
            "block_workers_on_open_questions",
            "maximum_open_questions",
            "maximum_note_chars",
        },
        "orchestration settings",
    )
    for key in ("teams_file", "knowledge_directory"):
        _relative_path(orchestration.get(key), f"orchestration.{key}")
    state_directory = _relative_path(raw["state"]["directory"], "state.directory")
    knowledge_directory = _relative_path(orchestration["knowledge_directory"], "orchestration.knowledge_directory")
    if tuple(knowledge_directory.parts[: len(state_directory.parts)]) != state_directory.parts:
        raise ValueError("orchestration.knowledge_directory must stay below state.directory")
    if (
        orchestration.get("require_manager") is not True
        or orchestration.get("block_workers_on_open_questions") is not True
    ):
        raise ValueError("manager and open-question blocking controls must remain enabled")
    _bounded_integer(orchestration, "maximum_open_questions", 1, 20)
    _bounded_integer(orchestration, "maximum_note_chars", 100, 10_000)

    context = _mapping(raw, "context")
    _exact_keys(context, {"maximum_brief_chars", "maximum_note_entries", "preserve_note_kinds"}, "context settings")
    _bounded_integer(context, "maximum_brief_chars", 500, 100_000)
    _bounded_integer(context, "maximum_note_entries", 1, 500)
    _string_list(context, "preserve_note_kinds")

    notifications = _mapping(raw, "notifications")
    _exact_keys(
        notifications,
        {"github_annotations", "maximum_records", "external_delivery_enabled", "reason"},
        "notification settings",
    )
    _bounded_integer(notifications, "maximum_records", 1, 200)
    if not isinstance(notifications.get("github_annotations"), bool):
        raise ValueError("notifications.github_annotations must be boolean")
    if notifications.get("external_delivery_enabled") is not False:
        raise ValueError("external notifications need a separately reviewed adapter")
    if not isinstance(notifications.get("reason"), str) or not notifications["reason"]:
        raise ValueError("notifications.reason must explain why external delivery is disabled")


def _validate_research(raw: dict[str, Any]) -> None:
    research = _mapping(raw, "research")
    _exact_keys(
        research,
        {
            "enabled",
            "provider",
            "searxng_url",
            "timeout_seconds",
            "maximum_response_bytes",
            "maximum_results",
            "allow_loopback",
            "fetch_allowed_hosts",
        },
        "research settings",
    )
    if not isinstance(research.get("enabled"), bool) or not isinstance(research.get("allow_loopback"), bool):
        raise ValueError("research enabled/allow_loopback settings must be boolean")
    if research.get("provider") != "searxng":
        raise ValueError("research.provider must be searxng")
    if not isinstance(research.get("searxng_url"), str):
        raise ValueError("research.searxng_url must be a string")
    _bounded_integer(research, "timeout_seconds", 1, 60)
    _bounded_integer(research, "maximum_response_bytes", 1_000, 2_000_000)
    _bounded_integer(research, "maximum_results", 1, 25)
    _string_list(research, "fetch_allowed_hosts", allow_empty=True)


def _validate_agents(raw: dict[str, Any]) -> None:
    agents = _mapping(raw, "agents")
    _exact_keys(
        agents,
        {
            "maximum_attempts_per_agent",
            "maximum_parallel_workers",
            "minimum_advanced_approvals",
            "require_final_judge",
            "stop_on",
        },
        "agent execution settings",
    )
    _bounded_integer(agents, "maximum_attempts_per_agent", 1, 20)
    _bounded_integer(agents, "maximum_parallel_workers", 1, 20)
    if _bounded_integer(agents, "minimum_advanced_approvals", 2, 10) != 2:
        raise ValueError("the shipped advanced aggregator currently requires exactly two role approvals")
    if _boolean(agents, "require_final_judge") is not True:
        raise ValueError("agents.require_final_judge cannot be disabled")
    _string_list(agents, "stop_on")


def _validate_verification(raw: dict[str, Any]) -> None:
    verification = _mapping(raw, "verification")
    _exact_keys(
        verification,
        {
            "timeout_seconds_per_command",
            "commands",
            "require_clean_schema_sync",
            "require_documentation_for_architecture_changes",
        },
        "verification settings",
    )
    _bounded_integer(verification, "timeout_seconds_per_command", 1, 1800)
    if not _boolean(verification, "require_clean_schema_sync") or not _boolean(
        verification, "require_documentation_for_architecture_changes"
    ):
        raise ValueError("schema sync and architecture documentation requirements cannot be disabled")
    commands = verification.get("commands", [])
    if not commands or not all(
        isinstance(command, list)
        and command
        and all(isinstance(argument, str) and argument and "\x00" not in argument for argument in command)
        for command in commands
    ):
        raise ValueError("verification commands must be non-empty argv arrays")


def _validate_providers(raw: dict[str, Any]) -> None:
    providers = _mapping(raw, "providers")
    _exact_keys(providers, {"local", "claude", "gemini", "jules"}, "provider settings")
    local = _mapping(providers, "local")
    _exact_keys(local, {"enabled", "priority", "transport"}, "local provider settings")
    if local.get("enabled") is not True or local.get("transport") != "loopback_only":
        raise ValueError("local provider must remain enabled and loopback-only")
    _bounded_integer(local, "priority", 1, 100)
    for provider_name in ("claude", "gemini", "jules"):
        provider = _mapping(providers, provider_name)
        _exact_keys(provider, {"enabled", "reason"}, f"{provider_name} provider settings")
        if (
            provider.get("enabled") is not False
            or not isinstance(provider.get("reason"), str)
            or not provider["reason"]
        ):
            raise ValueError(f"{provider_name} requires a disabled state and explanatory reason")


def _validate_schedules(raw: dict[str, Any]) -> None:
    schedules = _mapping(raw, "schedules")
    _exact_keys(schedules, {"enabled", "reason", "allowlisted_commands"}, "schedule settings")
    if schedules.get("enabled") is not False or not isinstance(schedules.get("reason"), str) or not schedules["reason"]:
        raise ValueError("schedule execution must remain disabled with an explanatory reason")
    if set(_string_list(schedules, "allowlisted_commands")) != {"/verify", "/status"}:
        raise ValueError("scheduled command proposals are limited to /verify and /status")


def _validate_execution(raw: dict[str, Any]) -> None:
    _validate_agents(raw)
    _validate_verification(raw)
    _validate_providers(raw)
    _validate_schedules(raw)


def load_settings(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load and validate the reviewed repository policy before any action."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("agent settings must be a mapping")
    missing = _REQUIRED_TOP_LEVEL - set(raw)
    extra = set(raw) - _REQUIRED_TOP_LEVEL
    if missing or extra:
        raise ValueError(f"agent settings keys mismatch; missing={sorted(missing)}, extra={sorted(extra)}")
    if raw.get("version") != 1 or raw.get("repository_language") != "English":
        raise ValueError("unsupported agent settings version or repository language")
    if raw.get("default_mode") not in {"standard", "advanced"}:
        raise ValueError("default_mode must be standard or advanced")
    for key in _REQUIRED_TOP_LEVEL - {"version", "repository_language", "default_mode"}:
        _mapping(raw, key)
    _validate_scope(raw)
    _validate_state_goal_approval(raw)
    state_directory = str(raw["state"]["directory"]).strip("/").removeprefix("./")
    ignored = {str(item).strip("/").removeprefix("./") for item in raw["scope"]["ignored_directories"]}
    if state_directory not in ignored:
        raise ValueError("state.directory must also be listed in scope.ignored_directories")
    _validate_change_limits(raw)
    _validate_orchestration(raw)
    _validate_research(raw)
    _validate_execution(raw)
    return raw


def safe_repo_path(value: str) -> str:
    """Normalize a repository-relative POSIX path or reject it."""
    if not value or any(ord(character) < 32 for character in value) or "\\" in value:
        raise ValueError(f"unsafe repository path: {value!r}")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or ".." in path.parts or path.parts[0] in {".git", ".vibeci"}:
        raise ValueError(f"unsafe repository path: {value!r}")
    clean = path.as_posix().removeprefix("./")
    if not clean or len(clean) > 300:
        raise ValueError(f"unsafe repository path: {value!r}")
    return clean


def path_scope_issue(path: str, settings: dict[str, Any]) -> str | None:
    """Return why a normalized path is hidden or outside configured scope."""
    clean = safe_repo_path(path)
    scope = settings["scope"]
    for configured in scope["ignored_directories"]:
        directory = configured.strip("/").removeprefix("./")
        if clean == directory or clean.startswith(directory + "/"):
            return f"path is inside ignored directory {configured}"
    suffix = PurePosixPath(clean).suffix.casefold()
    ignored_extensions = {item.casefold() for item in scope["ignored_extensions"]}
    if suffix and suffix in ignored_extensions:
        return f"path uses ignored extension {suffix}"
    if any(fnmatch.fnmatchcase(clean, pattern) for pattern in scope["denied_path_globs"]):
        return "path matches a denied path pattern"
    allowed = scope["allowed_path_globs"]
    if allowed and not any(fnmatch.fnmatchcase(clean, pattern) for pattern in allowed):
        return "path does not match an allowed path pattern"
    return None


def path_is_visible(path: str, settings: dict[str, Any]) -> bool:
    """Return whether a tracked path may enter planner or reviewer context."""
    try:
        return path_scope_issue(path, settings) is None
    except ValueError:
        return False
