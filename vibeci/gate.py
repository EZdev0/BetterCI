"""Validation gate. A proposal reaches GitHub only if the PATCHED file passes.

Principle: never trust a patch because it parses; require that it does not
make the file worse. Every language gets (1) a syntax check and (2) a
no-regression check against the original file.

No-regression is compared *per rule code*, not by total finding count: a patch
that fixes one F401 but introduces one S110 must not be accepted just because
the total stayed the same.
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass

from .policy import suspicious_added_lines


@dataclass
class GateResult:
    ok: bool
    reason: str


def apply_replacement(source: str, start: int, end: int, replacement: str) -> str:
    """Replace 1-based inclusive lines [start, end] of `source`."""
    lines = source.splitlines(keepends=True)
    if start < 1 or end > len(lines) or end < start:
        raise ValueError("line range outside file")
    new = replacement
    if new != "" and lines[end - 1].endswith("\n") and not new.endswith("\n"):
        new += "\n"
    return "".join(lines[: start - 1]) + new + "".join(lines[end:])


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False)


def _regression_reason(before: Counter[str] | None, after: Counter[str] | None, tool: str) -> str | None:
    """Reject when any rule code is more frequent in the patched file."""
    if before is None or after is None:
        return None
    added = {code: n - before[code] for code, n in after.items() if n > before[code]}
    if not added:
        return None
    detail = ", ".join(f"{code}+{n}" for code, n in sorted(added.items()))
    return f"new {tool} findings: {detail}"


def _py_ruff_codes(source: str, filename: str) -> Counter[str] | None:
    if not shutil.which("ruff"):
        return None
    proc = subprocess.run(
        ["ruff", "check", "--no-fix", "--exit-zero", "--output-format", "json", "--stdin-filename", filename, "-"],
        input=source, capture_output=True, text=True, timeout=30, check=False,
    )
    try:
        raw = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(raw, list):
        return None
    return Counter(item.get("code", "?") for item in raw if isinstance(item, dict))


def _shellcheck_codes(source: str, ext: str) -> Counter[str] | None:
    if not shutil.which("shellcheck"):
        return None
    with tempfile.NamedTemporaryFile("w", suffix=ext, delete=False, encoding="utf-8") as fh:
        fh.write(source)
        tmp = fh.name
    try:
        proc = _run(["shellcheck", "-f", "json1", tmp], timeout=30)
    finally:
        os.unlink(tmp)
    try:
        raw = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    comments = raw.get("comments", []) if isinstance(raw, dict) else []
    return Counter(f"SC{c.get('code', '?')}" for c in comments if isinstance(c, dict))


def _compiler_errors(path: str, source: str) -> int | None:
    cxx = path.endswith((".cc", ".cpp", ".cxx", ".hpp", ".hh"))
    exe = shutil.which("g++" if cxx else "gcc")
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as td:
        f = os.path.join(td, "t" + os.path.splitext(path)[1])
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(source)
        proc = _run([exe, "-fsyntax-only", "-w", "-fmax-errors=50", f], timeout=60)
    return sum(1 for ln in proc.stderr.splitlines() if ": error:" in ln or ": fatal error:" in ln)


def check_patched(path: str, original: str, patched: str) -> GateResult:
    """Return ok=True only if `patched` is a safe replacement for `original`."""
    if patched == original:
        return GateResult(False, "patch is a no-op")
    if len(original) > 400_000 or len(patched) > 400_000:
        return GateResult(False, "file too large for gate")
    original_lines = original.splitlines()
    patched_lines = patched.splitlines()
    before_lines = len(original_lines)
    deleted = sum(
        old_end - old_start
        for operation, old_start, old_end, _new_start, _new_end in difflib.SequenceMatcher(
            a=original_lines,
            b=patched_lines,
            autojunk=max(len(original_lines), len(patched_lines)) > 2000,
        ).get_opcodes()
        if operation in {"delete", "replace"}
    )
    if original.strip() and not patched.strip():
        return GateResult(False, "patch would empty an existing file")
    if deleted > 80 or (before_lines >= 20 and deleted / before_lines > 0.35):
        return GateResult(False, f"excessive line deletion: removed {deleted} of {before_lines} lines")
    patch = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), patched.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))
    risks = suspicious_added_lines(patch)
    if risks:
        return GateResult(False, f"security report only: {risks[0].code}; no generated fix")
    ext = os.path.splitext(path)[1].lower()

    if ext in (".py", ".pyi"):
        try:
            ast.parse(patched)
            compile(patched, path, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            return GateResult(False, f"python syntax: {exc}")
        why = _regression_reason(_py_ruff_codes(original, path), _py_ruff_codes(patched, path), "ruff")
        if why:
            return GateResult(False, why)
        return GateResult(True, "python ok")

    if ext in (".sh", ".bash"):
        with tempfile.NamedTemporaryFile("w", suffix=ext, delete=False, encoding="utf-8") as fh:
            fh.write(patched)
            tmp = fh.name
        try:
            if _run(["bash", "-n", tmp]).returncode != 0:
                return GateResult(False, "bash -n failed")
        finally:
            os.unlink(tmp)
        why = _regression_reason(_shellcheck_codes(original, ext), _shellcheck_codes(patched, ext), "shellcheck")
        if why:
            return GateResult(False, why)
        return GateResult(True, "shell syntax + shellcheck ok")

    if ext in (".yml", ".yaml"):
        try:
            import yaml

            yaml.safe_load(patched)
        # Any parse failure rejects.
        except Exception as exc:  # noqa: BLE001
            return GateResult(False, f"yaml: {str(exc)[:120]}")
        return GateResult(True, "yaml ok")

    if ext in (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh"):
        base, new = _compiler_errors(path, original), _compiler_errors(path, patched)
        if base is None or new is None:
            return GateResult(False, "no compiler available to verify C/C++ patch")
        if new > base:
            return GateResult(False, f"compiler errors increased {base}->{new}")
        return GateResult(True, "c/c++ no new errors")

    return GateResult(False, f"no verifier for {ext or 'unknown'} files")
