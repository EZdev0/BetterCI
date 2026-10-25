"""Deterministic policy checks for advanced agent worktrees.

Models may explain a change, but only this module decides whether its diff is
inside the approved plan. It never executes model-provided text.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from .safety import redact_sensitive_text
from .settings import path_scope_issue, safe_repo_path


@dataclass(frozen=True)
class PolicyIssue:
    code: str
    severity: str
    path: str
    message: str
    evidence: str


@dataclass
class PolicyReport:
    ok: bool
    report_only: bool
    baseline: str
    changed_files: list[str] = field(default_factory=list)
    issues: list[PolicyIssue] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "report_only": self.report_only,
            "baseline": self.baseline,
            "changed_files": self.changed_files,
            "issues": [asdict(issue) for issue in self.issues],
            "checks": self.checks,
        }


_DOC_SUFFIXES = {".md", ".rst", ".txt"}
_GERMAN_WORDS = {
    "aber",
    "ändern",
    "aufgabe",
    "datei",
    "diese",
    "dieser",
    "dieses",
    "einen",
    "einem",
    "eine",
    "fehler",
    "für",
    "geprüft",
    "hinzufügen",
    "keine",
    "löschen",
    "lösung",
    "nicht",
    "oder",
    "sicherheit",
    "soll",
    "und",
    "werden",
    "wird",
    "ziel",
}
_SUSPICIOUS = [
    ("PIPE_TO_SHELL", re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba|z|k)?sh\b", re.IGNORECASE)),
    ("DESTRUCTIVE_DELETE", re.compile(r"\brm\s+(?:-[A-Za-z]*r[A-Za-z]*f|-rf|-fr)\s+(?:/|~|\$HOME|\.)", re.IGNORECASE)),
    ("DYNAMIC_EXECUTION", re.compile(r"\b(?:eval|exec)\s*\(")),
    ("WORLD_WRITABLE", re.compile(r"\bchmod\s+(?:-R\s+)?777\b", re.IGNORECASE)),
    ("SECRET_EXPOSURE", re.compile(r"(?:secrets\.[A-Za-z_][\w]*|GITHUB_TOKEN).*(?:curl|wget|http)", re.IGNORECASE)),
]
_CRON_FIELD = re.compile(r"^[\d*/,\-]+$")
_CRON_LIMITS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def _expand_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    if not field or not _CRON_FIELD.fullmatch(field):
        raise ValueError("schedule requires numeric POSIX cron fields using only *, /, , and -")
    values: set[int] = set()
    for item in field.split(","):
        if not item:
            raise ValueError("cron lists cannot contain empty items")
        parts = item.split("/")
        if len(parts) > 2 or not parts[0]:
            raise ValueError(f"invalid cron item: {item}")
        step = 1
        if len(parts) == 2:
            if not parts[1].isdigit() or int(parts[1]) < 1:
                raise ValueError(f"invalid cron step: {item}")
            step = int(parts[1])
        base = parts[0]
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            bounds = base.split("-")
            if len(bounds) != 2 or not all(bound.isdigit() for bound in bounds):
                raise ValueError(f"invalid cron range: {item}")
            start, end = (int(bound) for bound in bounds)
        elif base.isdigit() and len(parts) == 1:
            start = end = int(base)
        else:
            raise ValueError(f"invalid cron item: {item}")
        if start < minimum or end > maximum or start > end or step > maximum - minimum + 1:
            raise ValueError(f"cron value outside {minimum}-{maximum}: {item}")
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError("cron field selects no values")
    return values


def validate_cron(expression: str) -> str:
    """Validate bounded five-field GitHub/POSIX cron syntax and ranges."""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("schedule requires a five-field POSIX cron expression")
    expanded = [_expand_cron_field(field, *limits) for field, limits in zip(fields, _CRON_LIMITS, strict=True)]
    minutes = sorted(expanded[0])
    if any(right - left < 5 for left, right in pairwise(minutes)):
        raise ValueError("GitHub scheduled workflows have a five-minute minimum interval")
    hours = expanded[1]
    adjacent_hours = any((hour + 1) % 24 in hours for hour in hours)
    if len(minutes) > 1 and adjacent_hours and 60 - minutes[-1] + minutes[0] < 5:
        raise ValueError("GitHub scheduled workflows have a five-minute minimum interval")
    return " ".join(fields)


def documentation_language_issue(text: str) -> str | None:
    """Flag clearly German prose while avoiding one-word false positives."""
    words = {word.casefold() for word in re.findall(r"[A-Za-zÄÖÜäöüß]+", text)}
    hits = sorted(words & _GERMAN_WORDS)
    has_german_character = bool(re.search(r"[äöüßÄÖÜ]", text))
    if len(hits) >= 3 or (has_german_character and hits):
        return f"documentation must be English; German markers: {', '.join(hits[:8])}"
    return None


def suspicious_added_lines(diff_text: str) -> list[PolicyIssue]:
    """Return report-only risks found in added lines of a unified diff."""
    path = ""
    issues: list[PolicyIssue] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        added = line[1:]
        for code, pattern in _SUSPICIOUS:
            if pattern.search(added):
                issues.append(
                    PolicyIssue(
                        code=code,
                        severity="high",
                        path=path or "(unknown)",
                        message="suspicious added code requires human security review; no auto-fix is allowed",
                        evidence=added[:240],
                    )
                )
    return issues


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc


def _baseline_text(cwd: Path, baseline: str, path: str) -> str:
    proc = _git(cwd, "show", f"{baseline}:{path}", check=False)
    return proc.stdout if proc.returncode == 0 else ""


def _line_count(text: str) -> int:
    return len(text.splitlines())


def _collect_change_data(cwd: Path, baseline: str) -> tuple[list[str], str, dict[str, int]]:
    """Collect tracked and untracked changes plus exact removed-line counts."""
    tracked_changes = _git(cwd, "diff", "--name-status", baseline, "--").stdout.splitlines()
    diff_text = _git(cwd, "diff", "--no-ext-diff", "--no-color", baseline, "--").stdout
    numstat = _git(cwd, "diff", "--numstat", baseline, "--").stdout
    deleted_lines = {
        columns[-1]: int(columns[1])
        for line in numstat.splitlines()
        if len(columns := line.split("\t")) >= 3 and columns[1].isdigit()
    }
    seen_paths = {line.split("\t")[-1] for line in tracked_changes if "\t" in line}
    status_lines = _git(cwd, "status", "--porcelain=v1", "--untracked-files=all").stdout.splitlines()
    for status_line in status_lines:
        if not status_line.startswith("?? "):
            continue
        untracked = status_line[3:]
        if untracked == ".vibeci-worktree.json" or untracked in seen_paths:
            continue
        tracked_changes.append(f"A\t{untracked}")
        candidate = cwd / untracked
        if candidate.is_file():
            try:
                content = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                content = ""
            added_lines = "".join(f"+{line}\n" for line in content.splitlines())
            diff_text += f"\n--- /dev/null\n+++ b/{untracked}\n{added_lines}"
    return tracked_changes, diff_text, deleted_lines


def _scope_issues(
    path: str,
    status: str,
    raw: str,
    allowed: set[str],
    change_cfg: dict[str, Any],
    settings: dict[str, Any],
) -> list[PolicyIssue]:
    """Check deletion, rename, approved-scope and protected-path policy."""
    issues: list[PolicyIssue] = []
    scope_problem = path_scope_issue(path, settings)
    if scope_problem:
        issues.append(PolicyIssue("OUTSIDE_AGENT_SCOPE", "critical", path, scope_problem, raw))
    if status.startswith("D") and change_cfg.get("forbid_file_deletion", True):
        issues.append(PolicyIssue("FILE_DELETED", "critical", path, "tracked file deletion is forbidden", raw))
    if status.startswith("R") and change_cfg.get("forbid_renames", True):
        issues.append(PolicyIssue("FILE_RENAMED", "high", path, "file renames require human handling", raw))
    if change_cfg.get("require_planned_paths", True) and path not in allowed:
        issues.append(PolicyIssue("UNPLANNED_PATH", "high", path, "path is not present in the approved plan", raw))
    for configured_path in change_cfg.get("protected_paths", []):
        protected = configured_path.rstrip("/")
        if path == protected or path.startswith(protected + "/"):
            issues.append(
                PolicyIssue(
                    "PROTECTED_PATH",
                    "critical",
                    path,
                    "agents cannot modify this protected path",
                    protected,
                )
            )
    return issues


def _deletion_budget_issue(
    cwd: Path,
    baseline: str,
    path: str,
    current: Path,
    deleted_lines: dict[str, int],
    change_cfg: dict[str, Any],
) -> PolicyIssue | None:
    """Reject actual removed lines, including replacements with no net line loss."""
    before = _line_count(_baseline_text(cwd, baseline, path))
    try:
        after = _line_count(current.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        after = before
    deleted = deleted_lines.get(path, max(before - after, 0))
    ratio = deleted / max(before, 1)
    if before > 0 and after == 0:
        return PolicyIssue(
            "EMPTY_FILE",
            "critical",
            path,
            "an existing non-empty file must not be emptied",
            f"before={before}, after={after}",
        )
    if deleted > int(change_cfg["maximum_deleted_lines_per_file"]) or (
        before >= 20 and ratio > float(change_cfg["maximum_deletion_ratio"])
    ):
        return PolicyIssue(
            "EXCESSIVE_DELETION",
            "critical",
            path,
            "removed lines exceed the restoration threshold",
            f"before={before}, after={after}, deleted={deleted}, ratio={ratio:.3f}",
        )
    return None


def _file_issues(
    cwd: Path,
    baseline: str,
    path: str,
    status: str,
    deleted_lines: dict[str, int],
    change_cfg: dict[str, Any],
) -> list[PolicyIssue]:
    """Check path containment, file size, language and deletion budgets."""
    current = cwd / path
    resolved_current = current.resolve()
    if resolved_current != cwd and cwd not in resolved_current.parents:
        return [
            PolicyIssue(
                "PATH_ESCAPE",
                "critical",
                path,
                "path resolves outside the worktree",
                str(resolved_current),
            )
        ]
    issues: list[PolicyIssue] = []
    if (
        status.startswith("A")
        and current.is_file()
        and current.stat().st_size > int(change_cfg["maximum_added_file_bytes"])
    ):
        issues.append(
            PolicyIssue(
                "ADDED_FILE_TOO_LARGE",
                "high",
                path,
                "new file exceeds the configured byte limit",
                str(current.stat().st_size),
            )
        )
    if (
        current.suffix.lower() in _DOC_SUFFIXES
        and current.is_file()
        and change_cfg.get(
            "require_english_documentation",
            True,
        )
    ):
        try:
            language_problem = documentation_language_issue(current.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            language_problem = "documentation is not valid UTF-8"
        if language_problem:
            issues.append(PolicyIssue("DOCUMENTATION_LANGUAGE", "high", path, language_problem, path))
    if current.is_file() and not status.startswith("A"):
        deletion_issue = _deletion_budget_issue(cwd, baseline, path, current, deleted_lines, change_cfg)
        if deletion_issue:
            issues.append(deletion_issue)
    return issues


def evaluate_changes(
    workspace: str | os.PathLike[str],
    baseline: str,
    planned_paths: set[str],
    settings: dict[str, Any],
) -> PolicyReport:
    """Evaluate committed and uncommitted changes against an approved plan."""
    cwd = Path(workspace).resolve()
    _git(cwd, "rev-parse", "--verify", f"{baseline}^{{commit}}")
    change_cfg = settings["changes"]
    allowed = {safe_repo_path(path) for path in planned_paths}
    tracked_changes, diff_text, deleted_lines = _collect_change_data(cwd, baseline)
    issues: list[PolicyIssue] = []
    changed: list[str] = []

    for raw in tracked_changes:
        columns = raw.split("\t")
        if len(columns) < 2:
            continue
        status, raw_path = columns[0], columns[-1]
        try:
            path = safe_repo_path(raw_path)
        except ValueError as exc:
            issues.append(PolicyIssue("UNSAFE_PATH", "critical", raw_path, str(exc), raw))
            continue
        changed.append(path)
        issues.extend(_scope_issues(path, status, raw, allowed, change_cfg, settings))
        issues.extend(_file_issues(cwd, baseline, path, status, deleted_lines, change_cfg))

    if not changed:
        issues.append(
            PolicyIssue(
                "NO_CHANGES",
                "high",
                "(repository)",
                "task produced no file changes",
                baseline,
            )
        )
    if len(set(changed)) > int(change_cfg["maximum_changed_files"]):
        issues.append(
            PolicyIssue(
                "TOO_MANY_FILES",
                "high",
                "(repository)",
                "changed-file count exceeds approved agent limit",
                f"changed={len(set(changed))}",
            )
        )
    risk_issues = suspicious_added_lines(diff_text)
    issues.extend(risk_issues)
    return PolicyReport(
        ok=not issues,
        report_only=bool(risk_issues),
        baseline=baseline,
        changed_files=sorted(set(changed)),
        issues=issues,
    )


def run_verification_commands(
    workspace: str | os.PathLike[str],
    settings: dict[str, Any],
    report: PolicyReport,
) -> PolicyReport:
    """Run only human-reviewed argv arrays from settings; never model text."""
    cwd = Path(workspace).resolve()
    timeout = int(settings["verification"]["timeout_seconds_per_command"])
    for command in settings["verification"]["commands"]:
        started = __import__("time").monotonic()
        try:
            proc = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            result = {
                "argv": command,
                "returncode": proc.returncode,
                "duration_seconds": round(__import__("time").monotonic() - started, 3),
                "output": redact_sensitive_text(proc.stdout + proc.stderr)[-2000:],
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = {
                "argv": command,
                "returncode": None,
                "duration_seconds": round(__import__("time").monotonic() - started, 3),
                "output": str(exc)[:500],
            }
        report.checks.append(result)
        if result["returncode"] != 0:
            report.issues.append(
                PolicyIssue(
                    "VERIFICATION_FAILED",
                    "high",
                    "(repository)",
                    "reviewed verification command failed",
                    json.dumps({"argv": command, "returncode": result["returncode"]}),
                )
            )
    report.ok = not report.issues
    return report
