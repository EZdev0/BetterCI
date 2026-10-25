"""CI guards for the repository itself.

These tests fail on unsafe workflow edits, stale schemas, unpinned actions and
lockfiles without hashes -- i.e. exactly the classes of error a CI job should
catch before they reach a release.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from vibeci.models import fix_schema
from vibeci.policy import documentation_language_issue
from vibeci.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.y*ml"))
SHA_PIN = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def _load(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_committed_fix_schema_matches_pydantic_model():
    with (ROOT / "schemas/fix.schema.json").open(encoding="utf-8") as fh:
        assert json.load(fh) == fix_schema(), "regenerate schemas/fix.schema.json from `python -m vibeci schema`"


def test_tools_lock_uses_full_hashes_and_pinned_revisions():
    data = json.loads((ROOT / "tools.lock.json").read_text(encoding="utf-8"))
    assert SHA256.fullmatch(data["llama_cpp"]["sha256"])
    assert "/releases/download/b" in data["llama_cpp"]["url"]
    for model in data["models"].values():
        assert SHA_PIN.fullmatch(model["revision"])
        assert SHA256.fullmatch(model["sha256"])


def test_lock_files_hash_every_pinned_package():
    for name in ("requirements.lock", "requirements-dev.lock"):
        text = (ROOT / name).read_text(encoding="utf-8")
        blocks = [b for b in re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text)
                  if re.match(r"[A-Za-z0-9_.-]+==", b)]
        assert blocks, f"{name} contains no pinned packages"
        for block in blocks:
            assert "--hash=sha256:" in block, (name, block.splitlines()[0])


def test_every_action_reference_is_a_full_commit_sha_with_version_comment():
    for path in [ROOT / "action.yml", *WORKFLOWS]:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "uses:" not in line:
                continue
            ref = line.split("uses:", 1)[1].strip().split("#")[0].strip()
            assert "@" in ref, (path.name, lineno, ref)
            assert SHA_PIN.fullmatch(ref.rsplit("@", 1)[1]), (path.name, lineno, ref)
            assert re.search(r"#\s*v?\d+\.\d+", line), (path.name, lineno, "missing version comment")


def test_workflows_are_least_privilege_bounded_and_injection_safe():
    assert WORKFLOWS, "no CI workflows found"
    for path in WORKFLOWS:
        data = _load(path)
        # PyYAML follows YAML 1.1 and parses the `on` key as boolean True.
        triggers = data.get("on", data.get(True))
        assert triggers, path.name
        assert "pull_request_target" not in triggers, path.name
        assert "workflow_run" not in triggers, path.name
        assert data.get("permissions") == {}, path.name
        assert "concurrency" in data, path.name
        for job_name, job in data["jobs"].items():
            assert job.get("timeout-minutes"), (path.name, job_name)
            assert job.get("permissions"), (path.name, job_name)
            for step in job.get("steps", []):
                if isinstance(step.get("run"), str):
                    assert "${{" not in step["run"], (path.name, job_name, "expression in run:")
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step.get("with", {}).get("persist-credentials") is False, (path.name, job_name)


def test_comment_workflow_is_read_only_and_default_branch_only():
    path = ROOT / ".github/workflows/vibeci-commands.yml"
    workflow = _load(path)
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"issue_comment": {"types": ["created"]}}
    assert workflow["permissions"] == {}

    job = workflow["jobs"]["diagnostics"]
    condition = job["if"]
    for required in (
        "github.event.issue.pull_request",
        "github.event.comment.author_association",
        "OWNER",
        "MEMBER",
        "COLLABORATOR",
    ):
        assert required in condition
    assert job["permissions"] == {
        "actions": "read",
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] <= 5

    text = path.read_text(encoding="utf-8")
    assert "pull_request_target" not in text
    assert "workflow_run" not in text
    assert "actions/cache" not in text
    assert "secrets." not in text
    assert "github.event.pull_request.head" not in text

    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {
        "ref": "${{ github.event.repository.default_branch }}",
        "fetch-depth": 1,
        "persist-credentials": False,
    }
    command = job["steps"][-1]
    assert command["run"] == "python3 -m vibeci.github_commands"
    assert command["env"] == {
        "GITHUB_API_URL": "https://api.github.com",
        "GITHUB_EVENT_PATH": "${{ github.event_path }}",
        "GITHUB_REPOSITORY": "${{ github.repository }}",
        "GITHUB_TOKEN": "${{ github.token }}",
    }


def test_action_metadata_is_complete():
    action = _load(ROOT / "action.yml")
    assert action["runs"]["using"] == "composite"
    for name, spec in action["inputs"].items():
        assert spec.get("description"), name
        assert "default" in spec, name
    for step in action["runs"]["steps"]:
        if "run" in step:
            assert step.get("shell") == "bash", step.get("name")


def test_readme_documents_all_action_inputs():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in _load(ROOT / "action.yml")["inputs"]:
        assert f"`{name}`" in readme, f"README is missing input {name}"


def test_all_repo_yaml_and_json_parse():
    paths = [ROOT / "action.yml", ROOT / "tools.lock.json", ROOT / "schemas/fix.schema.json",
             ROOT / ".github/dependabot.yml", ROOT / ".agents/settings.yml",
             ROOT / "grammars/fix.gbnf", *WORKFLOWS]
    for path in paths:
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix in (".yml", ".yaml"):
            assert isinstance(_load(path), dict), path
        else:
            assert path.read_text(encoding="utf-8").strip(), path


def test_agent_policy_is_tracked_but_runtime_state_is_ignored():
    settings = load_settings(ROOT / ".agents/settings.yml")
    assert settings["approval"]["required_before_writes"] is True
    assert settings["changes"]["forbid_file_deletion"] is True
    assert settings["agents"]["minimum_advanced_approvals"] >= 2
    assert ".eng" in settings["scope"]["ignored_extensions"]
    assert settings["orchestration"]["require_manager"] is True
    assert settings["research"]["enabled"] is False
    assert all(not spec["enabled"] for name, spec in settings["providers"].items() if name != "local")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".vibeci/" in ignore and ".agents/local/" in ignore and ".vibeci-worktree.json" in ignore
    entry = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for required in (".agents/README.md", ".agents/settings.yml", "RESEARCH.md"):
        assert required in entry


def test_documented_test_command_exposes_venv_tools_to_subprocesses():
    required = 'PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=. .venv/bin/python -m pytest -q tests'
    for path in (ROOT / "README.md", ROOT / "CONTRIBUTING.md"):
        text = path.read_text(encoding="utf-8")
        assert required in text, path
        assert "\nPYTHONPATH=. .venv/bin/python -m pytest" not in text, path


def test_every_bigbrain_team_has_a_manager_and_first_read_contract():
    teams = _load(ROOT / ".agents/bigbrain/teams.yml")["teams"]
    team_directories = {path.parent.name for path in (ROOT / ".agents/bigbrain/teams").glob("*/README.md")}
    assert team_directories == set(teams)
    managers = []
    for team_id, definition in teams.items():
        managers.append(definition["manager"])
        text = (ROOT / f".agents/bigbrain/teams/{team_id}/README.md").read_text(encoding="utf-8")
        assert f"`{definition['manager']}`" in text
        assert "AGENTS.md" in text
    assert len(managers) == len(set(managers))


def test_repository_docs_are_english_and_forbidden_product_reference_is_absent():
    for path in [*ROOT.glob("*.md"), *(ROOT / "docs").glob("*.md"), *(ROOT / ".agents").rglob("*.md")]:
        text = path.read_text(encoding="utf-8")
        assert documentation_language_issue(text) is None, path
        assert ("ver" + "cel") not in text.casefold(), path
