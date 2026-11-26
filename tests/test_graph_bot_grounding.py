"""Deterministic graph, rule-based bot and evidence-grounding regressions."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import mock_llm
from test_agent import _planned_controller

from vibeci import cli
from vibeci.bot import next_actions, route
from vibeci.graph import (
    LIFECYCLE_GRAPH,
    GraphEdge,
    GraphNode,
    WorkflowGraph,
    ready_tasks,
    task_graph,
)
from vibeci.grounding import (
    GroundedStatement,
    build_grounding,
    ledger_for,
    render_grounding,
    verify_grounding,
)
from vibeci.models import (
    AgentState,
    BlockingQuestion,
    Finding,
    GoalSpec,
    PlanProposal,
    PlanTask,
    Severity,
    Suggestion,
    TaskRuntime,
)
from vibeci.review import render_body

BASE = "import json\n\n\ndef load(path):\n    return 1\n"
HEAD = (
    "import json\nimport os\n\n\ndef load(path):\n"
    "    f = open(path)\n    data = json.load(f)\n    return data\n"
)


def test_workflow_graph_validates_edges_and_detects_cycles():
    graph = WorkflowGraph(
        [GraphNode("a", "a", "human"), GraphNode("b", "b", "deterministic")],
        [GraphEdge("a", "b", "done")],
        "a",
    )
    assert graph.topological_order() == ("a", "b")
    assert graph.frontier({"a"}) == (GraphEdge("a", "b", "done"),)
    assert "a -->|done| b" in graph.mermaid()
    cyclic = WorkflowGraph(
        [GraphNode("a", "a", "human"), GraphNode("b", "b", "deterministic")],
        [GraphEdge("a", "b", "forward"), GraphEdge("b", "a", "retry")],
        "a",
    )
    assert not cyclic.is_dag()
    with pytest.raises(ValueError, match="cycle"):
        cyclic.topological_order()
    with pytest.raises(ValueError, match="unknown node"):
        WorkflowGraph([GraphNode("a", "a", "human")], [GraphEdge("a", "ghost", "x")], "a")


def test_lifecycle_graph_is_connected_with_bounded_retry_edges():
    assert LIFECYCLE_GRAPH.is_dag() is False
    assert any(edge.event == "gate_failed" for edge in LIFECYCLE_GRAPH.edges)
    assert "flowchart LR" in LIFECYCLE_GRAPH.mermaid()


def _plan() -> PlanProposal:
    return PlanProposal(
        goal_id="a" * 12,
        summary="Two dependent tasks.",
        tasks=[
            PlanTask(id="first", title="First", description="Do the first task.", paths=["app.py"],
                     acceptance_checks=["one"], references=["app.py"]),
            PlanTask(id="second", title="Second", description="Do the second task.", paths=["app.py"],
                     dependencies=["first"], acceptance_checks=["two"], references=["app.py"]),
        ],
    )


def test_task_graph_ready_tasks_follow_verified_dependencies():
    runtimes = {"first": TaskRuntime(status="verified"), "second": TaskRuntime()}
    graph = task_graph(_plan(), runtimes)
    assert graph.is_dag()
    assert ready_tasks(_plan(), runtimes) == ("second",)
    assert "t-first" in graph.mermaid({})
    assert ready_tasks(None, {}) == ()
    assert "no plan" in task_graph(None, {}).mermaid()


def test_bot_blocks_unknown_events_and_open_questions():
    blocked = route("make_me_a_sandwich")
    assert blocked.blocked and blocked.command is None
    assert route("goal_set").blocked  # no goal exists, so no planner command is invented
    assert route("approved").blocked  # no approved plan exists
    state = AgentState(goal=None, questions=[])
    state.questions.append(BlockingQuestion(
        question_id="q-" + "1" * 12,
        team="security",
        prompt="Is this intentional?",
        trigger_codes=["HUMAN_QUESTION"],
        context=[],
        status="open",
        created_at="2026-01-01T00:00:00Z",
    ))
    decision = route("goal_set", state)
    assert decision.command == "/answer q-111111111111"
    assert decision.actor == "human" and decision.blocked
    assert next_actions(state)[0].command == "/answer q-111111111111"


def test_bot_without_planner_never_invents_a_plan():
    state = AgentState(goal=GoalSpec(objective="Harden parser", acceptance_criteria=["tests pass"],
                                     references=["app.py"]))
    actions = next_actions(state, planner_configured=False)
    assert actions[0].command == ""
    assert not actions[0].uses_model
    assert "loopback model" in actions[0].reason
    assert next_actions(state, planner_configured=True)[0].command == "/plan"


def test_controller_graph_next_and_bot_commands_are_read_only(tmp_path):
    controller = _planned_controller(tmp_path)
    before = controller.store.load().model_dump()
    graph = controller.handle("/graph")
    assert graph.ok and "flowchart LR" in graph.message
    assert graph.data["model_chooses_edges"] is False
    following = controller.handle("/next")
    assert following.ok
    assert following.data["model_routes_events"] is False
    assert following.data["actions"][0]["command"] == "/start update-value"
    decision = controller.handle("/bot bogus-event")
    assert not decision.ok and "Unknown event" in decision.message
    assert controller.store.load().model_dump() == before


def test_grounding_rejects_tampered_evidence():
    finding = Finding(tool="ruff", rule="SIM115", path="app.py", line=1, end_line=1,
                      message="Use a context manager", severity=Severity.warning)
    source = "f = open(path)\nreturn f.read()\n"
    report = build_grounding(finding, source, [1], "python ok")
    assert report.ok
    detail, refs, digest = render_grounding(report)
    assert "f = open(path)" in detail
    assert "app.py:1" in refs and digest is not None
    tampered = (*report.statements, GroundedStatement(kind="source_line", ref="app.py:1", value="invented"))
    ledger = ledger_for(finding, source, [1], ["gate:python ok"])
    second = verify_grounding(tampered, ledger)
    assert not second.ok and "source_line:app.py:1" in second.rejected
    assert render_grounding(second)[0].startswith("No verified evidence")


def test_published_llm_suggestion_contains_no_model_prose(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "app.py").write_text(BASE)
    git("add", "-A")
    git("commit", "-qm", "base")
    (tmp_path / "app.py").write_text(HEAD)
    git("commit", "-qam", "head")
    server, url = mock_llm.start()
    mock_llm.REPLY["value"] = {
        "propose_fix": {
            "verdict": "fix", "bug": "UNIQUE_UNVERIFIED_MODEL_CLAIM", "start_line": 6, "end_line": 7,
            "replacement": "    with open(path) as f:\n        data = json.load(f)\n", "confidence": "high",
            "edge_case": {"kind": "resource_leak", "scenario": "INVENTED_HALLUCINATED_SCENARIO"},
        },
    }
    try:
        assert cli.main(["scan", "--base", "HEAD~1", "--llm-url", url, "--transport", "grammar",
                         "--out", "out"]) == 0
    finally:
        server.shutdown()
    suggestions = json.loads((tmp_path / "out" / "review.json").read_text())["suggestions"]
    model_suggestion = next(item for item in suggestions if item["source"] == "llm")
    serialized = json.dumps(model_suggestion)
    assert "UNIQUE_UNVERIFIED_MODEL_CLAIM" not in serialized
    assert "INVENTED_HALLUCINATED_SCENARIO" not in serialized
    assert model_suggestion["grounding_sha256"]
    assert model_suggestion["confidence"] == "medium"  # deterministic, not the model's own rating
    assert "Verified evidence" in model_suggestion["detail"]
    body = render_body(Suggestion.model_validate(model_suggestion))
    assert "Evidence digest" in body and "confidence high" not in body
