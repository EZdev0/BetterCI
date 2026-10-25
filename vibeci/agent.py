"""Goal/plan/approval parser and isolated-worktree controller.

This is intentionally not a free-form coding agent. It is a deterministic
control plane around an optional loopback-only planner. Model text can propose
plans, but cannot approve, execute shell text, bypass paths or merge branches.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from . import agent_parser
from .agent_types import CommandResult
from .bigbrain import BigBrainCommands
from .knowledge import KnowledgeJournal
from .llm import LLMClient
from .models import AgentState, PlanTask, ScheduleProposal, TaskRuntime
from .orchestration import load_teams
from .policy import evaluate_changes, run_verification_commands, validate_cron
from .safety import classify_request, redact_sensitive_text
from .settings import load_settings

_HELP = """VibeCI agent commands:
/help
/goal <objective> --accept <criterion> [--accept ...] --ref <path|rule|issue|URL> [--ref ...]
/plan
/status
/approve <plan-id>   (alias: /ok <plan-id>)
/tasks
/start <task-id>
/verify <task-id>
/review standard|advanced
/restore <task-id>
/stop <agent-id>
/ask <team> -- "<question>" [--ref <context>]
/answer <question-id> -- "<answer>"
/teams
/brief <team> -- "<bounded request>"
/note <team> <decision|evidence|handoff|observation|warning> -- "<text>" --ref <evidence>
/context
/notifications [all|open]
/ack <notification-id>
/schedule "<five-field-cron>" -- /verify|/status

Planning is read-only. Writes require an approved plan and an isolated managed worktree. Open questions pause workers. /brief never launches a provider, and /approve never merges.

Policy-authorized GitHub issue/PR comments use a separate diagnostics-only adapter after its workflow is on the default branch:
/help | /ci | /runs | /run <run-id> | /runners | /failures
Data commands optionally accept --target default|pr:<number>. They show target-bound Actions status, assigned runners and failing steps; they cannot execute, rerun, cancel, approve or merge."""


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AgentState:
        if not self.path.exists():
            return AgentState()
        try:
            return AgentState.model_validate_json(self.path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_input=False)[:5]
            raise ValueError(f"invalid agent state {self.path}: schema errors {errors}") from exc
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid agent state {self.path}: {exc}") from exc

    def save(self, state: AgentState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = AgentState.model_validate(state.model_dump())
        payload = state.model_dump_json(indent=2) + "\n"
        fd, temporary = tempfile.mkstemp(prefix="agent-state-", suffix=".json", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc


def repository_root(start: str | os.PathLike[str] = ".") -> Path:
    proc = _git(Path(start).resolve(), "rev-parse", "--show-toplevel")
    return Path(proc.stdout.strip()).resolve()


class AgentController(BigBrainCommands):
    def __init__(
        self,
        root: Path,
        settings_path: Path | None = None,
        state_path: Path | None = None,
        planner: LLMClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.settings_path = settings_path or self.root / ".agents/settings.yml"
        self.settings = load_settings(self.settings_path)
        configured_state = self.settings["state"]["file"]
        self.store = StateStore(state_path or self.root / configured_state)
        self.planner = planner
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        teams_path = (self.root / self.settings["orchestration"]["teams_file"]).resolve()
        if teams_path != self.root and self.root not in teams_path.parents:
            raise ValueError("team settings must stay inside the repository")
        self.teams = load_teams(teams_path)
        self.knowledge = KnowledgeJournal(
            self.root,
            self.settings["orchestration"]["knowledge_directory"],
            set(self.teams),
            int(self.settings["orchestration"]["maximum_note_chars"]),
            self.clock,
        )

    def handle(self, text: str) -> CommandResult:
        try:
            command, args = agent_parser.parse_tokens(text)
            new_state = not self.store.path.exists()
            state = self.store.load()
            if new_state:
                state.mode = self.settings["default_mode"]
        except ValueError as exc:
            return CommandResult(False, False, str(exc))
        handler = getattr(self, f"_cmd_{command[1:].replace('-', '_')}", None)
        if handler is None:
            return CommandResult(False, False, f"Unknown command {command}. Use /help.")
        try:
            result = handler(state, args)
        except (ValueError, RuntimeError, OSError, ValidationError) as exc:
            return CommandResult(False, False, str(exc))
        if result.changed:
            try:
                self.store.save(state)
            except (OSError, ValueError, ValidationError) as exc:
                return CommandResult(
                    False,
                    False,
                    f"command could not persist agent state; inspect managed Git state before retrying: {exc}",
                    result.data,
                )
        return result

    def _cmd_help(self, _state: AgentState, args: list[str]) -> CommandResult:
        if args:
            raise ValueError("/help takes no arguments")
        return CommandResult(True, False, _HELP)

    def _cmd_goal(self, state: AgentState, args: list[str]) -> CommandResult:
        goal = agent_parser.goal_from_args(args)
        goal_settings = self.settings["goal"]
        if len(goal.acceptance_criteria) < int(goal_settings["minimum_acceptance_criteria"]):
            raise ValueError("goal has fewer acceptance criteria than repository policy requires")
        if len(goal.references) < int(goal_settings["minimum_references"]):
            raise ValueError("goal has fewer evidence references than repository policy requires")
        if len(goal.objective) > int(goal_settings["maximum_objective_chars"]) or any(
            len(criterion) > int(goal_settings["maximum_criterion_chars"])
            for criterion in goal.acceptance_criteria
        ):
            raise ValueError("goal text exceeds repository policy bounds")
        request_text = " ".join([goal.objective, *goal.acceptance_criteria, *goal.references])
        if redact_sensitive_text(request_text) != request_text:
            raise ValueError("goals and evidence references cannot contain credential-like data")
        signals = classify_request(request_text)
        if signals:
            question = self._record_question(
                state,
                "security",
                self._risk_prompt(signals),
                [signal.code for signal in signals],
                [signal.evidence for signal in signals],
            )
            return CommandResult(
                False,
                True,
                f"Goal was not recorded. Security question {question.question_id} must be answered, then submit a safe goal.",
                {"question": question.model_dump(), "signals": [signal.as_dict() for signal in signals]},
            )
        state.goal = goal
        state.plan = None
        state.approved_plan_id = None
        state.baseline_sha = None
        state.tasks = {}
        return CommandResult(True, True, f"Goal {goal.goal_id} recorded. Run /plan.", goal.model_dump())

    def _cmd_plan(self, state: AgentState, args: list[str]) -> CommandResult:
        if args:
            raise ValueError("/plan takes no arguments")
        if state.goal is None:
            raise ValueError("set an evidence-backed /goal before planning")
        self._require_no_open_questions(state)
        if self.planner is None:
            raise ValueError("/plan requires a configured loopback local model; no provider fallback is enabled")
        plan = self.planner.plan(state.goal, agent_parser.repo_manifest(self.root, self.settings))
        if plan is None:
            raise RuntimeError("local planner did not return a valid plan")
        state.plan = agent_parser.validate_plan(self.root, state.goal, plan, self.settings)
        state.approved_plan_id = None
        state.baseline_sha = None
        state.tasks = {task.id: TaskRuntime() for task in state.plan.tasks}
        return CommandResult(
            True,
            True,
            f"Plan {state.plan.plan_id} is ready. Review it, then /approve {state.plan.plan_id}.",
            state.plan.model_dump(),
        )

    def _cmd_status(self, state: AgentState, args: list[str]) -> CommandResult:
        if args:
            raise ValueError("/status takes no arguments")
        data = {
            "mode": state.mode,
            "goal_id": state.goal.goal_id if state.goal else None,
            "plan_id": state.plan.plan_id if state.plan else None,
            "approved_plan_id": state.approved_plan_id,
            "baseline_sha": state.baseline_sha,
            "tasks": {name: runtime.model_dump() for name, runtime in state.tasks.items()},
            "schedules": [schedule.model_dump() for schedule in state.schedules],
            "stopped_agents": state.stopped_agents,
            "open_questions": [question.model_dump() for question in self._open_questions(state)],
            "unacknowledged_notifications": sum(not item.acknowledged for item in state.notifications),
            "expert_teams": sorted(self.teams),
        }
        return CommandResult(True, False, json.dumps(data, indent=2), data)

    def _cmd_approve(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) != 1:
            raise ValueError("usage: /approve <plan-id>")
        if state.goal is None or state.plan is None or args[0] != state.plan.plan_id:
            raise ValueError("plan ID is missing or stale; review the current /status")
        self._require_no_open_questions(state)
        dirty = _git(self.root, "status", "--porcelain=v1").stdout.splitlines()
        if dirty:
            raise ValueError("controller worktree must be clean before plan approval")
        current_head = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        if any(runtime.attempt for runtime in state.tasks.values()) and state.baseline_sha != current_head:
            raise ValueError("cannot move an approval baseline after task attempts exist; create and review a new plan")
        reviewed_plan_id = state.plan.plan_id
        state.plan = agent_parser.validate_plan(self.root, state.goal, state.plan, self.settings)
        if state.plan.plan_id != reviewed_plan_id:
            raise ValueError("plan changed during approval revalidation; review the new plan digest")
        state.approved_plan_id = state.plan.plan_id
        state.baseline_sha = current_head
        return CommandResult(
            True, True, f"Plan {state.plan.plan_id} approved at {state.baseline_sha}. Approval does not merge."
        )

    def _cmd_ok(self, state: AgentState, args: list[str]) -> CommandResult:
        return self._cmd_approve(state, args)

    def _cmd_review(self, state: AgentState, args: list[str]) -> CommandResult:
        mode = args[0].casefold() if len(args) == 1 else ""
        if mode not in {"standard", "advanced"}:
            raise ValueError("usage: /review standard|advanced")
        if mode == "advanced" and (
            state.plan is None or state.approved_plan_id != state.plan.plan_id or state.baseline_sha is None
        ):
            raise ValueError("advanced review mode requires a currently approved plan")
        state.mode = mode
        return CommandResult(True, True, f"Review mode set to {state.mode}.")

    def _cmd_tasks(self, state: AgentState, args: list[str]) -> CommandResult:
        if args:
            raise ValueError("/tasks takes no arguments")
        if state.plan is None:
            raise ValueError("no plan exists")
        data = []
        for task in state.plan.tasks:
            data.append({**task.model_dump(), "runtime": state.tasks[task.id].model_dump()})
        return CommandResult(True, False, json.dumps(data, indent=2), {"tasks": data})

    def _require_approved_task(
        self,
        state: AgentState,
        task_id: str,
        *,
        require_dependencies: bool = True,
    ) -> tuple[PlanTask, TaskRuntime]:
        if (
            state.goal is None
            or state.plan is None
            or state.approved_plan_id != state.plan.plan_id
            or not state.baseline_sha
        ):
            raise ValueError("current plan is not approved")
        task = next((item for item in state.plan.tasks if item.id == task_id), None)
        if task is None:
            raise ValueError(f"unknown task: {task_id}")
        runtime = state.tasks[task_id]
        if require_dependencies:
            for dependency in task.dependencies:
                if state.tasks[dependency].status != "verified":
                    raise ValueError(f"task {task_id} is blocked by unverified dependency {dependency}")
        return task, runtime

    def _cmd_start(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) != 1:
            raise ValueError("usage: /start <task-id>")
        self._require_no_open_questions(state)
        task, runtime = self._require_approved_task(state, args[0])
        if runtime.status == "in_progress":
            raise ValueError(f"task {task.id} already has an active worktree")
        if runtime.status == "verified":
            raise ValueError(f"task {task.id} is already verified")
        if runtime.attempt and runtime.status != "restored":
            raise ValueError(f"restore task {task.id} before starting another attempt")
        current_head = _git(self.root, "rev-parse", "HEAD").stdout.strip()
        dirty = _git(self.root, "status", "--porcelain=v1").stdout.splitlines()
        if current_head != state.baseline_sha or dirty:
            raise ValueError(
                "approval baseline is stale or the controller worktree is dirty; re-approve before new tasks"
            )
        active = sum(item.status == "in_progress" for item in state.tasks.values())
        parallel_limit = int(self.settings["agents"]["maximum_parallel_workers"])
        if active >= parallel_limit:
            raise ValueError(f"maximum parallel worker limit reached ({parallel_limit})")
        maximum = int(self.settings["agents"]["maximum_attempts_per_agent"])
        attempt = runtime.attempt + 1
        if attempt > maximum:
            raise ValueError(f"task {task.id} exhausted its {maximum} attempts")
        worktree_root = (self.root / self.settings["state"]["worktrees"]).resolve()
        if self.root not in worktree_root.parents:
            raise ValueError("configured worktree directory must stay inside the repository")
        path = worktree_root / f"{state.goal.goal_id}-{task.id}-a{attempt}"
        branch = f"{self.settings['state']['branch_prefix']}{state.goal.goal_id}-{task.id}-a{attempt}"
        if (
            path.exists()
            or _git(self.root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0
        ):
            raise ValueError("managed worktree path or branch already exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(self.root, "worktree", "add", "-b", branch, str(path), state.baseline_sha)
        marker = {
            "version": 1,
            "controller_root": str(self.root),
            "task_id": task.id,
            "plan_id": state.plan.plan_id,
            "baseline_sha": state.baseline_sha,
            "branch": branch,
        }
        (path / ".vibeci-worktree.json").write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        runtime.status = "in_progress"
        runtime.worktree = str(path)
        runtime.branch = branch
        runtime.baseline_sha = state.baseline_sha
        runtime.attempt = attempt
        return CommandResult(True, True, f"Task {task.id} started in {path} on {branch}.", marker)

    def _managed_runtime(
        self,
        state: AgentState,
        task_id: str,
        runtime: TaskRuntime,
    ) -> tuple[Path, dict]:
        if state.plan is None or not runtime.worktree or not runtime.branch or not runtime.baseline_sha:
            raise ValueError("task has no managed worktree")
        path = Path(runtime.worktree).resolve()
        allowed_root = (self.root / self.settings["state"]["worktrees"]).resolve()
        if allowed_root not in path.parents:
            raise ValueError("refusing operation outside the managed worktree directory")
        marker_path = path / ".vibeci-worktree.json"
        if marker_path.is_symlink():
            raise ValueError("managed worktree marker must not be a symlink")
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("managed worktree marker is missing or invalid") from exc
        expected_marker = {
            "version": 1,
            "controller_root": str(self.root),
            "task_id": task_id,
            "plan_id": state.plan.plan_id,
            "baseline_sha": runtime.baseline_sha,
            "branch": runtime.branch,
        }
        if marker != expected_marker:
            raise ValueError("managed worktree marker does not match task, plan, branch and baseline state")
        branch = _git(path, "branch", "--show-current").stdout.strip()
        if branch != runtime.branch:
            raise ValueError("managed worktree branch does not match recorded state")
        if not branch.startswith(self.settings["state"]["branch_prefix"]):
            raise ValueError("refusing operation on a non-agent branch")
        if Path(_git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != path:
            raise ValueError("worktree root mismatch")
        return path, marker

    def _cmd_verify(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) != 1:
            raise ValueError("usage: /verify <task-id>")
        task, runtime = self._require_approved_task(state, args[0])
        if runtime.status != "in_progress":
            raise ValueError(f"task {task.id} is not an active attempt; start or restore it before verification")
        path, _marker = self._managed_runtime(state, task.id, runtime)
        planned = set(task.paths) | set(task.creates)
        report = evaluate_changes(path, runtime.baseline_sha, planned, self.settings)
        if report.ok:
            report = run_verification_commands(path, self.settings, report)
        runtime.status = "verified" if report.ok else "failed"
        runtime.evidence.append(json.dumps(report.as_dict(), sort_keys=True)[:4000])
        state.last_report = report.as_dict()
        if not report.ok:
            self._emit(
                state,
                "error",
                f"Task {task.id} verification failed",
                f"Review policy report evidence for baseline {runtime.baseline_sha} before restore or retry.",
            )
        label = "verified" if report.ok else "rejected"
        if report.report_only:
            label += " (security report only; no generated fix)"
        return CommandResult(report.ok, True, f"Task {task.id} {label}.", report.as_dict())

    def _cmd_restore(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) != 1:
            raise ValueError("usage: /restore <task-id>")
        task, runtime = self._require_approved_task(state, args[0], require_dependencies=False)
        path, marker = self._managed_runtime(state, task.id, runtime)
        if marker.get("baseline_sha") != runtime.baseline_sha:
            raise ValueError("worktree baseline marker does not match recorded state")
        before = _git(path, "diff", "--stat", runtime.baseline_sha, "--").stdout.strip()
        _git(path, "reset", "--hard", runtime.baseline_sha)
        runtime.status = "restored"
        runtime.evidence.append(f"restored tracked files to {runtime.baseline_sha}; previous diff: {before[:1000]}")
        return CommandResult(
            True, True, f"Task {args[0]} restored to {runtime.baseline_sha}. Untracked files were preserved."
        )

    def _cmd_stop(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) != 1 or not args[0].replace("-", "").isalnum():
            raise ValueError("usage: /stop <agent-id>")
        if args[0] not in state.stopped_agents:
            state.stopped_agents.append(args[0])
        if args[0] in state.tasks:
            state.tasks[args[0]].status = "stopped"
        return CommandResult(True, True, f"Agent {args[0]} is stopped; later output from that ID must be ignored.")

    def _cmd_schedule(self, state: AgentState, args: list[str]) -> CommandResult:
        if "--" not in args:
            raise ValueError('usage: /schedule "<five-field-cron>" -- /verify|/status')
        separator = args.index("--")
        cron = validate_cron(" ".join(args[:separator]))
        command_tokens = [token.casefold() for token in args[separator + 1 :]]
        if len(command_tokens) != 1 or command_tokens[0] not in self.settings["schedules"]["allowlisted_commands"]:
            raise ValueError("scheduled command is not allowlisted")
        if len(state.schedules) >= 20:
            raise ValueError("schedule proposal limit reached")
        proposal = ScheduleProposal(cron=cron, command=command_tokens[0])
        state.schedules.append(proposal)
        return CommandResult(
            True,
            True,
            "Schedule proposal recorded but disabled. A human-reviewed workflow PR is required; no cron job was created.",
            proposal.model_dump(),
        )
