"""Explicit workflow graphs for the deterministic control plane.

The model never selects an edge. Nodes declare who is allowed to act (human,
deterministic code or a bounded model role), and edges declare the event that
moves work forward. This module does not execute anything: it describes the
state machine, validates it and renders it. The controller owns all writes.

Two graph shapes are supported:

* the lifecycle graph, which intentionally contains bounded retry cycles;
* a task dependency graph from an approved plan, which must be a DAG.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from .models import AgentState, PlanProposal, TaskRuntime

NodeRole = Literal["human", "deterministic", "model", "terminal"]
NodeStatus = Literal["pending", "active", "done", "blocked"]

_ENTRY = "__entry__"
_DONE = "__done__"


@dataclass(frozen=True)
class GraphNode:
    id: str
    label: str
    role: NodeRole

    def mermaid(self, status: str | None = None) -> str:
        suffix = f" ({status})" if status else ""
        text = f"{self.label}{suffix}".replace('"', "'")
        if self.role == "terminal":
            return f'{self.id}(["{text}"])'
        if self.role == "human":
            return f'{self.id}["{text}"]'
        if self.role == "model":
            return f'{self.id}[/"{text}"/]'
        return f'{self.id}["{text}"]'


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    event: str
    guard: str = ""

    def mermaid(self) -> str:
        label = self.event if not self.guard else f"{self.event} [{self.guard}]"
        return f'{self.source} -->|{label.replace("|", "/")}| {self.target}'


class WorkflowGraph:
    """A small validated graph with explicit entry point and event edges."""

    def __init__(self, nodes: Iterable[GraphNode], edges: Iterable[GraphEdge], entry: str) -> None:
        self.nodes = tuple(nodes)
        self.edges = tuple(edges)
        self.entry = entry
        self.validate()

    def validate(self) -> None:
        ids = [node.id for node in self.nodes]
        duplicates = [node_id for node_id, count in Counter(ids).items() if count > 1]
        if duplicates:
            raise ValueError(f"duplicate graph nodes: {sorted(duplicates)}")
        known = set(ids)
        if self.entry not in known:
            raise ValueError(f"graph entry {self.entry!r} is not a node")
        event_keys: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(f"graph edge references an unknown node: {edge}")
            if not edge.event or any(ord(char) < 32 for char in edge.event):
                raise ValueError("graph edges require a bounded event name")
            key = (edge.source, edge.target, edge.event)
            if key in event_keys:
                raise ValueError(f"duplicate graph edge: {key}")
            event_keys.add(key)
        reachable = {self.entry}
        queue = deque([self.entry])
        outgoing: dict[str, list[str]] = defaultdict(list)
        for edge in self.edges:
            outgoing[edge.source].append(edge.target)
        while queue:
            node = queue.popleft()
            for target in outgoing[node]:
                if target not in reachable:
                    reachable.add(target)
                    queue.append(target)
        unreachable = sorted(known - reachable)
        if unreachable:
            raise ValueError(f"unreachable graph nodes: {unreachable}")

    def is_dag(self) -> bool:
        """Return True when the graph contains no directed cycle."""
        try:
            self.topological_order()
        except ValueError:
            return False
        return True

    def topological_order(self) -> tuple[str, ...]:
        """Return a stable topological order; raise when a cycle exists."""
        indegree = {node.id: 0 for node in self.nodes}
        outgoing: dict[str, list[GraphEdge]] = defaultdict(list)
        for edge in self.edges:
            indegree[edge.target] += 1
            outgoing[edge.source].append(edge)
        ready = deque(node.id for node in self.nodes if indegree[node.id] == 0)
        order: list[str] = []
        while ready:
            node_id = ready.popleft()
            order.append(node_id)
            for edge in sorted(outgoing[node_id], key=lambda item: item.target):
                indegree[edge.target] -= 1
                if indegree[edge.target] == 0:
                    ready.append(edge.target)
        if len(order) != len(self.nodes):
            raise ValueError("graph contains a directed cycle")
        return tuple(order)

    def frontier(self, completed: Iterable[str]) -> tuple[GraphEdge, ...]:
        """Return event edges leaving completed nodes toward unfinished work."""
        done = set(completed)
        return tuple(
            edge for edge in self.edges if edge.source in done and edge.target not in done
        )

    def mermaid(self, status: Mapping[str, str] | None = None) -> str:
        known = {node.id for node in self.nodes}
        lines = ["flowchart LR"]
        for node in self.nodes:
            state = status.get(node.id, "") if status else ""
            if state not in {"", "pending", "active", "done", "blocked"}:
                raise ValueError(f"invalid graph status {state!r}")
            lines.append(f"  {node.mermaid(state)}")
        for edge in self.edges:
            if edge.source in known and edge.target in known:
                lines.append(f"  {edge.mermaid()}")
        return "\n".join(lines)


LIFECYCLE_GRAPH = WorkflowGraph(
    nodes=[
        GraphNode("goal", "goal: evidence-backed", "human"),
        GraphNode("plan", "plan: read-only proposal", "model"),
        GraphNode("approve", "approve: human digest", "human"),
        GraphNode("start", "start: isolated worktree", "deterministic"),
        GraphNode("worker", "worker: bounded task", "deterministic"),
        GraphNode("verify", "verify: deny-first gate", "deterministic"),
        GraphNode("review", "review: bounded roles", "model"),
        GraphNode("done", "done: human merge", "terminal"),
        GraphNode("blocked", "blocked: human decision", "terminal"),
    ],
    edges=[
        GraphEdge("goal", "plan", "goal_set"),
        GraphEdge("plan", "approve", "plan_ready"),
        GraphEdge("approve", "start", "approved"),
        GraphEdge("start", "worker", "worktree_ready"),
        GraphEdge("worker", "verify", "worktree_changed"),
        GraphEdge("verify", "review", "gate_passed"),
        GraphEdge("verify", "worker", "gate_failed", "attempts_remain"),
        GraphEdge("verify", "blocked", "gate_failed", "attempts_exhausted"),
        GraphEdge("review", "verify", "review_rejected"),
        GraphEdge("review", "done", "reviews_approved"),
    ],
    entry="goal",
)


def task_graph(plan: PlanProposal | None, runtimes: Mapping[str, TaskRuntime]) -> WorkflowGraph:
    """Build the approved plan's dependency graph as a DAG."""
    if plan is None:
        return WorkflowGraph(
            [GraphNode(_ENTRY, "no plan", "terminal")], (), _ENTRY
        )
    nodes = [GraphNode(_ENTRY, "approved plan", "deterministic")]
    for task in plan.tasks:
        runtime = runtimes.get(task.id)
        label = task.title if runtime is None else f"{task.title}: {runtime.status}"
        nodes.append(GraphNode(f"t-{task.id}", label, "deterministic"))
    nodes.append(GraphNode(_DONE, "integration: human merge", "terminal"))
    edges = []
    depended_on = {dependency for task in plan.tasks for dependency in task.dependencies}
    for task in plan.tasks:
        predecessors = task.dependencies or [_ENTRY]
        for predecessor in predecessors:
            source = _ENTRY if predecessor == _ENTRY else f"t-{predecessor}"
            edges.append(GraphEdge(source, f"t-{task.id}", "dependency_satisfied"))
        if task.id not in depended_on:
            edges.append(GraphEdge(f"t-{task.id}", _DONE, "verified"))
    graph = WorkflowGraph(nodes, edges, _ENTRY)
    if not graph.is_dag():
        raise ValueError("plan task graph must be acyclic")
    return graph


def ready_tasks(plan: PlanProposal | None, runtimes: Mapping[str, TaskRuntime]) -> tuple[str, ...]:
    """Return deterministic next task IDs; no model participates in scheduling."""
    if plan is None:
        return ()
    pending = {
        task.id
        for task in plan.tasks
        if runtimes.get(task.id, TaskRuntime()).status in {"pending", "restored"}
    }
    return tuple(
        task.id
        for task in plan.tasks
        if task.id in pending and all(runtimes[dependency].status == "verified" for dependency in task.dependencies)
    )


def graph_report(state: AgentState) -> dict[str, object]:
    """Render lifecycle and task graph state without mutating the repository."""
    approved = state.plan is not None and state.approved_plan_id == state.plan.plan_id
    lifecycle_status: dict[str, str] = {}
    if state.goal is None:
        lifecycle_status["goal"] = "pending"
    else:
        lifecycle_status["goal"] = "done"
        lifecycle_status["plan"] = "done" if state.plan else "active"
    if state.plan:
        lifecycle_status["approve"] = "done" if approved else "pending"
        if approved:
            lifecycle_status["start"] = "active"
            statuses = {runtime.status for runtime in state.tasks.values()}
            if "in_progress" in statuses:
                lifecycle_status["worker"] = "active"
            elif "verified" in statuses:
                lifecycle_status["verify"] = "done"
                lifecycle_status["review"] = "active"
                lifecycle_status["done"] = "pending"
            elif "failed" in statuses:
                lifecycle_status["verify"] = "blocked"
    task_status = {
        f"t-{task_id}": runtime.status for task_id, runtime in sorted(state.tasks.items())
    }
    tasks = task_graph(state.plan, state.tasks)
    return {
        "lifecycle": LIFECYCLE_GRAPH.mermaid(lifecycle_status),
        "tasks": tasks.mermaid(task_status),
        "frontier": [
            edge.event
            for edge in LIFECYCLE_GRAPH.frontier(
                node_id for node_id, state in lifecycle_status.items() if state == "done"
            )
        ],
        "ready_tasks": list(ready_tasks(state.plan, state.tasks)),
        "deterministic": True,
        "model_chooses_edges": False,
    }
