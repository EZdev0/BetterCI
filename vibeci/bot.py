"""Rule-based bot decisions for the agent control plane.

This is deliberately not an LLM router. Every decision comes from an explicit
event table and the validated agent state. The bot never executes a command,
never approves a plan and never picks a task by semantic guesswork: it returns
one bounded, allowlisted command for a human or the deterministic controller to
run. Unknown or ambiguous events are blocked.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from .graph import graph_report, ready_tasks
from .models import AgentState

Actor = Literal["human", "deterministic", "model"]

_EVENTS = {
    "goal_set",
    "plan_ready",
    "approved",
    "worktree_changed",
    "verification_failed",
    "verification_passed",
    "review_rejected",
    "reviews_approved",
    "open_question",
    "status_requested",
    "graph_requested",
}


@dataclass(frozen=True)
class BotAction:
    command: str
    actor: Actor
    reason: str
    uses_model: bool = False
    evidence: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BotDecision:
    event: str
    command: str | None
    actor: Actor | None
    reason: str
    blocked: bool
    uses_model: bool = False
    evidence: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def render(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)


def _question_id(state: AgentState) -> str | None:
    return next((question.question_id for question in state.questions if question.status == "open"), None)


def next_actions(
    state: AgentState,
    *,
    planner_configured: bool = False,
    maximum_parallel: int = 1,
) -> tuple[BotAction, ...]:
    """Return the safe next commands in strict priority order."""
    question = _question_id(state)
    if question:
        return (BotAction(
            f"/answer {question}",
            "human",
            "An open blocking question pauses every worker; policy cannot be answered by a model.",
            evidence=f"question:{question}",
        ),)

    if state.goal is None:
        return (BotAction("/goal <objective> --accept <criterion> --ref <evidence>", "human",
                          "No evidence-backed goal exists; the bot does not invent objectives."),)

    if state.plan is None:
        if not planner_configured:
            return (BotAction(
                "",
                "deterministic",
                "Planning requires an explicitly configured loopback model; the bot stays idle instead of inventing a plan.",
                evidence="state.plan is None",
            ),)
        return (BotAction("/plan", "model", "A read-only planner may propose a plan; validation and approval remain human.",
                          uses_model=True, evidence=f"goal:{state.goal.goal_id}"),)

    if state.plan is None or state.approved_plan_id != state.plan.plan_id:
        return (BotAction(f"/approve {state.plan.plan_id}", "human",
                          "Only a human may approve the immutable plan digest.",
                          evidence=f"plan:{state.plan.plan_id}"),)

    actions: list[BotAction] = []
    active = [task_id for task_id, runtime in state.tasks.items() if runtime.status == "in_progress"]
    for task_id in sorted(active):
        actions.append(BotAction(
            f"/verify {task_id}", "deterministic",
            "An active worktree can only be judged by the reviewed deny-first gate.",
            evidence=f"task:{task_id}:in_progress",
        ))
    failed = [task_id for task_id, runtime in state.tasks.items()
              if runtime.status == "failed" and task_id not in state.stopped_agents]
    for task_id in sorted(failed):
        actions.append(BotAction(
            f"/restore {task_id}", "deterministic",
            "A failed attempt must be restored in its marker-validated worktree before another attempt.",
            evidence=f"task:{task_id}:failed",
        ))
    if not failed:
        available_slots = maximum_parallel - len(active)
        for task_id in ready_tasks(state.plan, state.tasks)[:max(available_slots, 0)]:
            actions.append(BotAction(
                f"/start {task_id}", "deterministic",
                "Dependencies are verified; start creates one isolated worktree and launches no provider.",
                evidence=f"task:{task_id}:dependencies_verified",
            ))
    if not actions:
        actions.append(BotAction(
            "/status", "deterministic",
            "No safe worker transition is available; report state instead of guessing.",
            evidence="state:no_ready_transition",
        ))
    actions.append(BotAction(
        "", "human", "Merge remains a human decision; the bot has no merge command.",
        evidence="policy:human_merge_required",
    ))
    return tuple(actions)


def route(event: str, state: AgentState | None = None, detail: str = "") -> BotDecision:
    """Resolve one bounded event to one command; never execute it."""
    if event not in _EVENTS:
        return BotDecision(event, None, None, "Unknown event; no command was selected.", True)
    current = state or AgentState()
    question = _question_id(current)
    if question and event not in {"open_question", "status_requested", "graph_requested"}:
        return BotDecision(
            event, f"/answer {question}", "human",
            "Open human question blocks every other event.", True, evidence=f"question:{question}",
        )
    if event == "goal_set":
        if current.goal is None:
            return BotDecision(event, None, None, "No recorded goal; the bot does not invent one.", True)
        return BotDecision(event, "/plan", "model", "Planning is a bounded model role.", False, True,
                           "goal_set")
    if event == "plan_ready":
        plan_id = current.plan.plan_id if current.plan else None
        if plan_id is None:
            return BotDecision(event, None, None, "No validated plan exists to approve.", True)
        return BotDecision(event, f"/approve {plan_id}", "human", "Plan approval binds a SHA-256 digest.", False,
                           False, f"plan:{plan_id}")
    if event == "approved":
        if current.plan is None or current.approved_plan_id != current.plan.plan_id:
            return BotDecision(event, None, None, "No approved plan; the bot cannot schedule a task.", True)
        ready = ready_tasks(current.plan, current.tasks)
        if not ready:
            return BotDecision(event, None, None, "No task has verified dependencies.", True)
        return BotDecision(event, f"/start {ready[0]}", "deterministic",
                           "Deterministic scheduler picked the first ready task.", False, False,
                           f"task:{ready[0]}")
    if event == "worktree_changed":
        candidates = [task_id for task_id, runtime in current.tasks.items() if runtime.status == "in_progress"]
        if not candidates:
            return BotDecision(event, None, None, "No active managed worktree exists.", True)
        return BotDecision(event, f"/verify {candidates[0]}", "deterministic",
                           "Verification is deterministic and deny-first.", False, False,
                           f"task:{candidates[0]}")
    if event == "verification_failed":
        candidates = [task_id for task_id, runtime in current.tasks.items() if runtime.status == "failed"]
        if not candidates or current.plan is None:
            return BotDecision(event, None, None, "No failed attempt is available to restore.", True)
        return BotDecision(event, f"/restore {candidates[0]}", "deterministic",
                           "Only the managed worktree of that attempt is restored.", False, False,
                           f"task:{candidates[0]}")
    if event in {"verification_passed", "review_rejected", "reviews_approved"}:
        return BotDecision(event, "/status", "deterministic",
                           "Status is read-only; review roles and human merge stay separate.", False, False,
                           "state:status")
    if event == "open_question":
        if question is None:
            return BotDecision(event, None, None, "No open question exists.", True)
        return BotDecision(event, f"/answer {question}", "human",
                           "Only a human may answer a blocking question.", False, False, f"question:{question}")
    if event == "status_requested":
        return BotDecision(event, "/status", "deterministic", "Read-only state report.", False, False, "state")
    if event == "graph_requested":
        report = graph_report(current)
        return BotDecision(event, "/graph", "deterministic", "Graph is rendered from validated state.", False,
                           False, f"ready_tasks={','.join(str(item) for item in report['ready_tasks'])}")
    return BotDecision(event, None, None, "Unhandled event.", True)


def bot_report(state: AgentState, *, planner_configured: bool = False,
               maximum_parallel: int = 1) -> dict[str, object]:
    """Machine-readable bot status; all values derive from validated state."""
    actions = next_actions(state, planner_configured=planner_configured, maximum_parallel=maximum_parallel)
    return {
        "mode": "deterministic_rules",
        "model_routes_events": False,
        "actions": [action.as_dict() for action in actions],
        "graph": graph_report(state),
    }
