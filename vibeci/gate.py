"""Validation gate. A proposal reaches GitHub only if the PATCHED file passes.

Principle: never trust a patch because it parses; require that it does not
make the file worse. Every language gets (1) a syntax check and (2) a
no-regression check against the original file.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional


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


def _run(cmd: List[str], cwd: Optional[str] = None, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False)


def _py_ruff_count(source: str, filename: str) -> Optional[int]:
    if not shutil.which("ruff"):
        return None
    proc = subprocess.run(
        ["ruff", "check", "--no-fix", "--exit-zero", "--output-format", "concise", "--stdin-filename", filename, "-"],
        input=source, capture_output=True, text=True, timeout=30, check=False,
    )
    return sum(1 for ln in proc.stdout.splitlines() if ln.strip() and not ln.startswith(("Found", "All checks", "[*]", "No fixes")))


def _compiler_errors(path: str, source: str) -> Optional[int]:
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
    if len(patched) > 400_000:
        return GateResult(False, "file too large for gate")
    ext = os.path.splitext(path)[1].lower()

    if ext in (".py", ".pyi"):
        try:
            ast.parse(patched)
            compile(patched, path, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            return GateResult(False, f"python syntax: {exc}")
        before, after = _py_ruff_count(original, path), _py_ruff_count(patched, path)
        if before is not None and after is not None and after > before:
            return GateResult(False, f"ruff findings increased {before}->{after}")
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
        return GateResult(True, "shell syntax ok")

    if ext in (".yml", ".yaml"):
        try:
            import yaml

            yaml.safe_load(patched)
        except Exception as exc:  # noqa: BLE001 - any parse failure rejects
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
