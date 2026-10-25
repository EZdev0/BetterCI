"""Tests for the goal/plan/approval parser and deterministic agent guards."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

from vibeci.agent import AgentController
from vibeci.evaluation import evaluate_model
from vibeci.models import FixProposal, PlanProposal, PlanTask
from vibeci.policy import documentation_language_issue, evaluate_changes, load_settings, validate_cron

ROOT = Path(__file__).resolve().parents[1]


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "agent@example.invalid")
    _git(tmp_path, "config", "user.name", "Agent Test")
    (tmp_path / ".gitignore").write_text(
        ".vibeci/\n.vibeci-worktree.json\nagent-settings.yml\n", encoding="utf-8",
    )
    (tmp_path / "app.py").write_text("".join(f"value_{n} = {n}\n" for n in range(100)), encoding="utf-8")
    (tmp_path / "README.md").write_text("# Example\n\nEnglish documentation.\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "baseline")

    settings = load_settings(ROOT / ".agents/settings.yml")
    settings["changes"]["protected_paths"] = [".git", ".agents/settings.yml"]
    settings["verification"]["commands"] = [[sys.executable, "-c", "print('verified')"]]
    settings_path = tmp_path / "agent-settings.yml"
    settings_path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    return tmp_path, settings_path


class Planner:
    def plan(self, goal, _manifest):
        return PlanProposal(
            goal_id=goal.goal_id,
            summary="Change the requested value and verify it.",
            tasks=[PlanTask(
                id="update-value",
                title="Update value",
                description="Apply the bounded source change.",
                paths=["app.py"],
                acceptance_checks=[goal.acceptance_criteria[0]],
                references=["app.py"],
            )],
            risks=["Unexpected line loss"],
            documentation_paths=[],
        )


class HallucinatingPlanner:
    def plan(self, goal, _manifest):
        return PlanProposal(
            goal_id=goal.goal_id,
            summary="Invent a path that is not in the repository.",
            tasks=[PlanTask(
                id="bad-path",
                title="Bad path",
                description="Touch a path without evidence.",
                paths=["missing.py"],
                acceptance_checks=["It works"],
                references=[goal.references[0]],
            )],
        )


class IncompletePlanner:
    def plan(self, goal, _manifest):
        return PlanProposal(
            goal_id=goal.goal_id,
            summary="Return a shaped plan that omits required goal evidence.",
            tasks=[PlanTask(
                id="incomplete",
                title="Incomplete task",
                description="Use a real path but omit the exact goal criterion.",
                paths=["app.py"],
                acceptance_checks=["A different check passes"],
                references=[goal.references[0]],
            )],
        )


class OutOfScopeDocumentationPlanner(Planner):
    def plan(self, goal, _manifest):
        plan = super().plan(goal, _manifest)
        plan.documentation_paths = ["README.md"]
        return plan


def _planned_controller(tmp_path: Path, planner=None):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings, planner=planner or Planner())
    assert controller.handle(
        '/goal Change one value safely --accept "The reviewed verification command passes" --ref app.py'
    ).ok
    plan_result = controller.handle("/plan")
    assert plan_result.ok, plan_result.message
    plan_id = controller.store.load().plan.plan_id
    assert controller.handle(f"/approve {plan_id}").ok
    return controller


def test_goal_requires_acceptance_and_reference(tmp_path):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings)
    result = controller.handle("/goal Change the parser")
    assert not result.ok
    assert "acceptance criterion" in result.message
    assert controller.handle("/help").ok


def test_vibeci_prefix_is_case_insensitive_and_never_evaluates_shell_text(tmp_path):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings)
    assert controller.handle("/vibeci HeLp").ok
    marker = root / "should-not-exist"
    result = controller.handle(
        '/vibeci goal "Do not execute $(touch should-not-exist)" '
        '--accept "No shell text is executed" --ref app.py'
    )
    assert result.ok and not marker.exists()
    rejected = controller.handle('/goal Change parser --unknown value --accept "Pass" --ref app.py')
    assert not rejected.ok and "unknown /goal option" in rejected.message


def test_corrupt_cross_field_state_returns_an_error_instead_of_crashing(tmp_path):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings)
    controller.store.path.parent.mkdir(parents=True, exist_ok=True)
    controller.store.path.write_text('{"version": 1, "mode": "standard", "tasks": {"ghost": {}}}', encoding="utf-8")
    result = controller.handle("/status")
    assert not result.ok and "invalid agent state" in result.message


def test_planner_rejects_hallucinated_paths(tmp_path):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings, planner=HallucinatingPlanner())
    assert controller.handle('/goal Fix parser --accept "Parser is correct" --ref app.py').ok
    result = controller.handle("/plan")
    assert not result.ok
    assert "missing paths" in result.message


def test_plan_must_cover_goal_criteria_and_documentation_scope(tmp_path):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings, planner=IncompletePlanner())
    assert controller.handle('/goal Fix parser --accept "Parser is correct" --ref app.py').ok
    missing = controller.handle("/plan")
    assert not missing.ok and "omits goal acceptance criteria" in missing.message

    controller = AgentController(root, settings_path=settings, planner=OutOfScopeDocumentationPlanner())
    assert controller.handle('/goal Fix parser --accept "Parser is correct" --ref app.py').ok
    out_of_scope = controller.handle("/plan")
    assert not out_of_scope.ok and "documentation paths are outside task scope" in out_of_scope.message


def test_advanced_controller_mode_requires_approved_plan(tmp_path):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings, planner=Planner())
    assert not controller.handle("/review advanced").ok
    assert controller.handle('/goal Fix parser --accept "Parser is correct" --ref app.py').ok
    assert controller.handle("/plan").ok
    plan_id = controller.store.load().plan.plan_id
    assert controller.handle(f"/approve {plan_id}").ok
    assert controller.handle("/review advanced").ok


def test_stale_plan_cannot_be_approved(tmp_path):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings, planner=Planner())
    assert controller.handle('/goal Fix parser --accept "Parser is correct" --ref app.py').ok
    assert controller.handle("/plan").ok
    assert not controller.handle("/approve deadbeefdeadbeef").ok


def test_approval_revalidates_plan_against_current_clean_commit(tmp_path):
    root, settings = _repository(tmp_path)
    controller = AgentController(root, settings_path=settings, planner=Planner())
    assert controller.handle('/goal Fix parser --accept "Parser is correct" --ref app.py').ok
    assert controller.handle("/plan").ok
    plan_id = controller.store.load().plan.plan_id
    _git(root, "rm", "app.py")
    _git(root, "commit", "-qm", "remove planned path")
    result = controller.handle(f"/approve {plan_id}")
    assert not result.ok and "missing paths" in result.message


def test_each_task_gets_isolated_worktree_and_restore_preserves_untracked_files(tmp_path):
    controller = _planned_controller(tmp_path)
    started = controller.handle("/start update-value")
    assert started.ok, started.message
    runtime = controller.store.load().tasks["update-value"]
    worktree = Path(runtime.worktree)
    assert worktree != tmp_path and _git(worktree, "branch", "--show-current").startswith("vibeci/task-")

    (worktree / "app.py").write_text("value_0 = 0\n", encoding="utf-8")
    (worktree / "notes.local").write_text("preserve me", encoding="utf-8")
    rejected = controller.handle("/verify update-value")
    assert not rejected.ok
    assert any(issue["code"] == "EXCESSIVE_DELETION" for issue in rejected.data["issues"])

    restored = controller.handle("/restore update-value")
    assert restored.ok
    assert len((worktree / "app.py").read_text(encoding="utf-8").splitlines()) == 100
    assert (worktree / "notes.local").read_text(encoding="utf-8") == "preserve me"


def test_marker_identity_and_stopped_attempt_fail_closed(tmp_path):
    controller = _planned_controller(tmp_path)
    assert controller.handle("/start update-value").ok
    runtime = controller.store.load().tasks["update-value"]
    worktree = Path(runtime.worktree)

    assert controller.handle("/stop update-value").ok
    stopped = controller.handle("/verify update-value")
    assert not stopped.ok and "not an active attempt" in stopped.message
    retry = controller.handle("/start update-value")
    assert not retry.ok and "restore" in retry.message

    marker_path = worktree / ".vibeci-worktree.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["task_id"] = "different-task"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    tampered = controller.handle("/restore update-value")
    assert not tampered.ok and "marker does not match" in tampered.message

    marker["task_id"] = "update-value"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    assert controller.handle("/restore update-value").ok
    restarted = controller.handle("/start update-value")
    assert restarted.ok
    assert controller.store.load().tasks["update-value"].attempt == 2


def test_verified_task_runs_only_reviewed_argv_commands(tmp_path):
    controller = _planned_controller(tmp_path)
    assert controller.handle("/start update-value").ok
    worktree = Path(controller.store.load().tasks["update-value"].worktree)
    text = (worktree / "app.py").read_text(encoding="utf-8")
    (worktree / "app.py").write_text(text.replace("value_50 = 50", "value_50 = 500"), encoding="utf-8")
    result = controller.handle("/verify update-value")
    assert result.ok, json.dumps(result.data, indent=2)
    assert result.data["checks"][0]["argv"][1:] == ["-c", "print('verified')"]
    assert controller.store.load().tasks["update-value"].status == "verified"


def test_policy_switches_suspicious_code_to_report_only(tmp_path):
    root, settings_path = _repository(tmp_path)
    baseline = _git(root, "rev-parse", "HEAD")
    with (root / "app.py").open("a", encoding="utf-8") as handle:
        handle.write("result = eval(user_input)\n")
    report = evaluate_changes(root, baseline, {"app.py"}, load_settings(settings_path))
    assert not report.ok and report.report_only
    assert any(issue.code == "DYNAMIC_EXECUTION" for issue in report.issues)


def test_policy_counts_removed_lines_even_when_replacements_hide_net_loss(tmp_path):
    root, settings_path = _repository(tmp_path)
    baseline = _git(root, "rev-parse", "HEAD")
    lines = (root / "app.py").read_text(encoding="utf-8").splitlines(keepends=True)
    lines[:81] = [f"replacement_{index} = {index}\n" for index in range(81)]
    (root / "app.py").write_text("".join(lines), encoding="utf-8")

    report = evaluate_changes(root, baseline, {"app.py"}, load_settings(settings_path))

    assert not report.ok
    issue = next(issue for issue in report.issues if issue.code == "EXCESSIVE_DELETION")
    assert "deleted=81" in issue.evidence


def test_documentation_language_guard_avoids_single_word_false_positive():
    assert documentation_language_issue("The command was rejected and logged.") is None
    issue = documentation_language_issue("Diese Datei wird nicht gelöscht und die Lösung ist sicher.")
    assert issue and "German markers" in issue


def test_schedule_parser_is_bounded():
    assert validate_cron("17 3 * * 1") == "17 3 * * 1"
    assert validate_cron("*/5 * * * 1-5") == "*/5 * * * 1-5"
    for invalid in (
        "@daily",
        "* * *",
        "$(touch /tmp/x) * * * *",
        "61 3 * * 1",
        "* * * * *",
        "1,3 3 * * 1",
        "17 3 * 13 1",
    ):
        try:
            validate_cron(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid cron accepted: {invalid}")


def test_model_evaluation_records_real_gate_and_semantic_results(tmp_path):
    class FakeClient:
        timeout = 5.0
        transport = "test"

        def propose(self, finding, _source, _deadline):
            replacements = {
                "F401": (1, 1, ""),
                "SIM115": (2, 3, "    with open(path) as handle:\n        return handle.read()\n"),
                "SC2086": (2, 2, 'echo "$name"'),
            }
            start, end, replacement = replacements[finding.rule]
            proposal = FixProposal(
                verdict="fix",
                bug="The deterministic smoke case is resolved.",
                start_line=start,
                end_line=end,
                replacement=replacement,
                confidence="high",
            )
            return proposal, 1, 10

    output = tmp_path / "evaluation.json"
    document = evaluate_model(FakeClient(), "fake-test-model", output)
    assert document["passed"] == document["total"] == 3
    assert json.loads(output.read_text(encoding="utf-8"))["disclaimer"].startswith("Smoke evaluation")
