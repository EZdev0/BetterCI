"""Fail-closed parsing and repository grounding for agent commands and plans."""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .models import GoalSpec, PlanProposal, PlanTask
from .policy import safe_repo_path


def parse_tokens(text: str) -> tuple[str, list[str]]:
    """Parse one slash command without invoking a shell."""
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError(f"invalid command quoting: {exc}") from exc
    if not tokens:
        raise ValueError("empty command")
    if tokens[0].casefold() == "/vibeci":
        tokens = tokens[1:]
        if not tokens:
            return "/help", []
        if not tokens[0].startswith("/"):
            tokens[0] = "/" + tokens[0]
    command = tokens[0].casefold()
    if not command.startswith("/"):
        raise ValueError("commands must start with '/' (or '/vibeci')")
    return command, tokens[1:]


def goal_from_args(args: list[str]) -> GoalSpec:
    """Build a goal while requiring one value per evidence option."""
    objective: list[str] = []
    criteria: list[str] = []
    references: list[str] = []
    destination = objective
    for argument in args:
        if argument == "--accept":
            destination = criteria
            destination.append("")
        elif argument == "--ref":
            destination = references
            destination.append("")
        elif argument.startswith("--"):
            raise ValueError(f"unknown /goal option: {argument}")
        elif destination is objective:
            objective.append(argument)
        elif not destination or destination[-1]:
            raise ValueError("each --accept and --ref takes exactly one quoted value")
        else:
            destination[-1] = argument
    if not objective:
        raise ValueError("/goal requires an objective")
    if not criteria or not references:
        raise ValueError("/goal requires at least one acceptance criterion and one evidence reference")
    if any(not value for value in [*criteria, *references]):
        raise ValueError("--accept and --ref require a value")
    return GoalSpec(objective=" ".join(objective), acceptance_criteria=criteria, references=references)


def _tracked_paths(root: Path) -> set[str]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {proc.stderr.strip()[:300]}")
    return set(proc.stdout.splitlines())


def repo_manifest(root: Path) -> str:
    tracked = sorted(_tracked_paths(root))
    lines = ["Tracked repository paths (authoritative; no path outside this list exists unless declared in creates):"]
    lines.extend(f"- {path}" for path in tracked[:1000])
    if len(tracked) > 1000:
        lines.append(f"- (manifest truncated; {len(tracked) - 1000} additional tracked paths are not available to the planner)")
    return "\n".join(lines)


def _validate_task_paths(
    root_paths: set[str],
    goal_refs: set[str],
    task: PlanTask,
) -> tuple[set[str], set[str], set[str]]:
    task.paths = [safe_repo_path(path) for path in task.paths]
    task.creates = [safe_repo_path(path) for path in task.creates]
    missing = set(task.paths) - root_paths
    already_exists = set(task.creates) & root_paths
    if missing:
        raise ValueError(f"task {task.id} references missing paths: {sorted(missing)}")
    if already_exists:
        raise ValueError(f"task {task.id} declares existing paths as new: {sorted(already_exists)}")
    evidence_scope = goal_refs | set(task.paths) | set(task.creates)
    unsupported = [
        ref for ref in task.references
        if not any(ref == item or ref.startswith(item + ":") for item in evidence_scope)
    ]
    if unsupported:
        raise ValueError(f"task {task.id} has unsupported references: {unsupported}")
    return set(task.paths) | set(task.creates), set(task.references), set(task.acceptance_checks)


def validate_plan(root: Path, goal: GoalSpec, plan: PlanProposal) -> PlanProposal:
    """Reject stale goals, hallucinated scope and incomplete goal evidence."""
    if plan.goal_id != goal.goal_id:
        raise ValueError(f"plan goal_id {plan.goal_id} does not match current goal {goal.goal_id}")
    tracked = _tracked_paths(root)
    goal_refs = set(goal.references)
    task_scope: set[str] = set()
    cited_references: set[str] = set()
    acceptance_checks: set[str] = set()
    for task in plan.tasks:
        scope, references, checks = _validate_task_paths(tracked, goal_refs, task)
        task_scope.update(scope)
        cited_references.update(references)
        acceptance_checks.update(checks)
    missing_criteria = set(goal.acceptance_criteria) - acceptance_checks
    if missing_criteria:
        raise ValueError(f"plan omits goal acceptance criteria: {sorted(missing_criteria)}")
    missing_references = goal_refs - cited_references
    if missing_references:
        raise ValueError(f"plan omits goal references: {sorted(missing_references)}")
    plan.documentation_paths = [safe_repo_path(path) for path in plan.documentation_paths]
    out_of_scope_docs = set(plan.documentation_paths) - task_scope
    if out_of_scope_docs:
        raise ValueError(f"documentation paths are outside task scope: {sorted(out_of_scope_docs)}")
    return plan
