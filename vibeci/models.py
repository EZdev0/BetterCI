"""Pydantic models. These are the single source of truth for every structure
that crosses a trust boundary (tool output -> LLM -> GitHub review).

The JSON schema exported from `FixProposal` is what llama-server turns into a
GBNF grammar. A grammar guarantees SYNTAX only; semantics are checked by
`vibeci.gate`.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"


class Finding(BaseModel):
    """One normalized result from a deterministic scanner."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    rule: str
    path: str
    line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    message: str
    severity: Severity = Severity.warning
    # A fix produced by the scanner itself (no LLM involved). Whole replacement
    # for lines [line..end_line], or None when the scanner has no fix.
    tool_fix: str | None = None

    @model_validator(mode="after")
    def _order(self) -> Finding:
        if self.end_line < self.line:
            self.end_line = self.line
        return self


class EdgeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "none_value",
        "syscall_failure",
        "race_condition",
        "resource_leak",
        "bounds",
        "encoding",
        "injection",
        "other",
    ]
    scenario: str = Field(min_length=3, max_length=240)


class FixProposal(BaseModel):
    """What the model is allowed to say. Nothing else can be produced."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["fix", "no_fix", "needs_human"]
    bug: str = Field(min_length=3, max_length=240)
    edge_case: EdgeCase | None = None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    replacement: str | None = Field(default=None, max_length=4000)
    confidence: Literal["high", "medium", "low"]

    @model_validator(mode="after")
    def _consistent(self) -> FixProposal:
        if self.end_line < self.start_line:
            raise ValueError("end_line < start_line")
        if self.verdict == "fix" and self.replacement is None:
            raise ValueError("verdict=fix requires replacement")
        if self.verdict != "fix" and self.replacement is not None:
            raise ValueError("replacement only allowed with verdict=fix")
        return self


class Suggestion(BaseModel):
    """A fully validated, postable inline suggestion."""

    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    replacement: str
    title: str
    detail: str
    source: Literal["tool", "llm"]
    rule: str
    confidence: Literal["high", "medium", "low"] = "high"
    evidence: list[str] = Field(default_factory=list, max_length=8)
    reviewer_summary: str | None = Field(default=None, max_length=600)

    @property
    def fingerprint(self) -> str:
        import hashlib

        raw = f"{self.path}:{self.start_line}:{self.end_line}:{self.rule}:{self.replacement}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class GoalSpec(BaseModel):
    """An immutable, evidence-backed objective for an advanced agent run."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=5, max_length=500)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    references: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _clean_goal(self) -> GoalSpec:
        self.objective = " ".join(self.objective.strip().split())
        self.acceptance_criteria = _unique_clean(self.acceptance_criteria, 300)
        self.references = _unique_clean(self.references, 500)
        if len(self.objective) < 5:
            raise ValueError("goal objective is empty or too short")
        if not self.acceptance_criteria or not self.references:
            raise ValueError("goal requires acceptance criteria and references")
        return self

    @property
    def goal_id(self) -> str:
        import hashlib
        import json

        raw = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:12]


class PlanTask(BaseModel):
    """One bounded task proposed by a read-only planner."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=5, max_length=500)
    paths: list[str] = Field(default_factory=list, max_length=30)
    creates: list[str] = Field(default_factory=list, max_length=30)
    acceptance_checks: list[str] = Field(min_length=1, max_length=20)
    dependencies: list[str] = Field(default_factory=list, max_length=20)
    references: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _clean_task(self) -> PlanTask:
        self.title = " ".join(self.title.strip().split())
        self.description = " ".join(self.description.strip().split())
        if len(self.title) < 3 or len(self.description) < 5:
            raise ValueError("task title or description is empty or too short")
        self.paths = _unique_clean(self.paths, 300)
        self.creates = _unique_clean(self.creates, 300)
        self.acceptance_checks = _unique_clean(self.acceptance_checks, 300)
        self.dependencies = _unique_clean(self.dependencies, 32)
        self.references = _unique_clean(self.references, 500)
        if not self.paths and not self.creates:
            raise ValueError("task needs at least one existing or new path")
        if not self.acceptance_checks:
            raise ValueError("task needs at least one non-empty acceptance check")
        if not self.references:
            raise ValueError("task needs at least one non-empty evidence reference")
        return self


class PlanProposal(BaseModel):
    """A complete plan. The model proposes it; deterministic code validates it."""

    model_config = ConfigDict(extra="forbid")

    goal_id: str = Field(pattern=r"^[0-9a-f]{12}$")
    summary: str = Field(min_length=5, max_length=600)
    tasks: list[PlanTask] = Field(min_length=1, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=20)
    documentation_paths: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _task_graph(self) -> PlanProposal:
        self.summary = " ".join(self.summary.strip().split())
        if len(self.summary) < 5:
            raise ValueError("plan summary is empty or too short")
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task IDs must be unique")
        known: set[str] = set()
        for task in self.tasks:
            missing = set(task.dependencies) - set(ids)
            if missing:
                raise ValueError(f"unknown dependencies for {task.id}: {sorted(missing)}")
            if any(dep not in known for dep in task.dependencies):
                raise ValueError(f"dependencies for {task.id} must appear earlier in the plan")
            known.add(task.id)
        self.risks = _unique_clean(self.risks, 300)
        self.documentation_paths = _unique_clean(self.documentation_paths, 300)
        return self

    @property
    def plan_id(self) -> str:
        import hashlib
        import json

        raw = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


class PatchReview(BaseModel):
    """Read-only reviewer verdict for one already-gated patch."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approve", "reject", "needs_human"]
    goal_satisfied: bool
    behavior_preserved: bool
    finding_resolved: bool
    new_risk: Literal["none", "low", "medium", "high", "unknown"]
    reason: str = Field(min_length=3, max_length=500)
    evidence: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _clean_review(self) -> PatchReview:
        self.reason = " ".join(self.reason.strip().split())
        self.evidence = _unique_clean(self.evidence, 300)
        if len(self.reason) < 3 or not self.evidence:
            raise ValueError("patch review requires a reason and evidence")
        return self

    @property
    def approved(self) -> bool:
        return (
            self.verdict == "approve"
            and self.goal_satisfied
            and self.behavior_preserved
            and self.finding_resolved
            and self.new_risk == "none"
        )


class TaskRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "in_progress", "verified", "failed", "stopped", "restored"] = "pending"
    worktree: str | None = None
    branch: str | None = None
    baseline_sha: str | None = None
    attempt: int = Field(default=0, ge=0, le=20)
    evidence: list[str] = Field(default_factory=list, max_length=50)


class ScheduleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cron: str
    command: Literal["/verify", "/status"]
    approved: bool = False


class BlockingQuestion(BaseModel):
    """A human clarification that pauses managed worker starts."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=r"^q-[0-9a-f]{12}$")
    team: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    prompt: str = Field(min_length=5, max_length=800)
    trigger_codes: list[str] = Field(min_length=1, max_length=10)
    context: list[str] = Field(default_factory=list, max_length=10)
    status: Literal["open", "answered"] = "open"
    answer: str | None = Field(default=None, max_length=1200)
    created_at: str = Field(min_length=20, max_length=32)
    answered_at: str | None = Field(default=None, min_length=20, max_length=32)

    @model_validator(mode="after")
    def _consistent_question(self) -> BlockingQuestion:
        self.prompt = " ".join(self.prompt.strip().split())
        self.trigger_codes = _unique_clean(self.trigger_codes, 80)
        self.context = _unique_clean(self.context, 500)
        if self.status == "open" and (self.answer is not None or self.answered_at is not None):
            raise ValueError("open questions cannot contain an answer")
        if self.status == "answered":
            if self.answer is None or self.answered_at is None:
                raise ValueError("answered questions require an answer and timestamp")
            self.answer = " ".join(self.answer.strip().split())
            if not self.answer:
                raise ValueError("question answer cannot be empty")
        return self


class AgentNotification(BaseModel):
    """A local durable notification; external delivery is a separate adapter."""

    model_config = ConfigDict(extra="forbid")

    notification_id: str = Field(pattern=r"^n-[0-9a-f]{12}$")
    level: Literal["notice", "warning", "error"]
    title: str = Field(min_length=3, max_length=120)
    message: str = Field(min_length=3, max_length=1000)
    link: str | None = Field(default=None, max_length=1000)
    created_at: str = Field(min_length=20, max_length=32)
    acknowledged: bool = False


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    mode: Literal["standard", "advanced"] = "standard"
    goal: GoalSpec | None = None
    plan: PlanProposal | None = None
    approved_plan_id: str | None = None
    baseline_sha: str | None = None
    tasks: dict[str, TaskRuntime] = Field(default_factory=dict)
    schedules: list[ScheduleProposal] = Field(default_factory=list, max_length=20)
    stopped_agents: list[str] = Field(default_factory=list, max_length=50)
    questions: list[BlockingQuestion] = Field(default_factory=list, max_length=20)
    notifications: list[AgentNotification] = Field(default_factory=list, max_length=200)
    last_report: dict | None = None

    @model_validator(mode="after")
    def _consistent_state(self) -> AgentState:
        if self.plan is None:
            if self.tasks or self.approved_plan_id is not None or self.baseline_sha is not None:
                raise ValueError("agent state without a plan cannot contain tasks or approval state")
        else:
            expected = {task.id for task in self.plan.tasks}
            if set(self.tasks) != expected:
                raise ValueError("agent task runtime keys do not match the current plan")
            if self.approved_plan_id is not None and self.approved_plan_id != self.plan.plan_id:
                raise ValueError("approved plan ID is stale")
        if (self.approved_plan_id is None) != (self.baseline_sha is None):
            raise ValueError("plan approval and baseline SHA must be recorded together")
        self.stopped_agents = _unique_clean(self.stopped_agents, 80)
        if len({question.question_id for question in self.questions}) != len(self.questions):
            raise ValueError("question IDs must be unique")
        if len({notice.notification_id for notice in self.notifications}) != len(self.notifications):
            raise ValueError("notification IDs must be unique")
        return self


def _unique_clean(values: list[str], maximum: int) -> list[str]:
    out: list[str] = []
    for value in values:
        clean = " ".join(value.strip().split())
        if len(clean) > maximum:
            raise ValueError(f"value exceeds {maximum} characters")
        if clean and clean not in out:
            out.append(clean)
    return out


def _schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def fix_schema() -> dict:
    """JSON schema handed to llama-server (-> GBNF) and to the tool-use API."""
    return _schema(FixProposal)


def plan_schema() -> dict:
    return _schema(PlanProposal)


def patch_review_schema() -> dict:
    return _schema(PatchReview)
