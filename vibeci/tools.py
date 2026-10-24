"""Deterministic scanners. Every parser is defensive: a broken tool must never
break the pipeline, it only yields no findings (and a log line)."""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .models import Finding, Severity

MAX_FILE_BYTES = 300_000


def log(msg: str) -> None:
    print(f"[vibeci] {msg}", file=sys.stderr, flush=True)


def _run(cmd: list[str], timeout: int = 120, cwd: str | None = None) -> subprocess.CompletedProcess | None:
    if not shutil.which(cmd[0]):
        log(f"tool not installed, skipping: {cmd[0]}")
        return None
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False)
    except subprocess.TimeoutExpired:
        log(f"timeout: {' '.join(cmd[:3])}")
        return None


def _read(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _changed_region(old: str, new: str) -> list[tuple]:
    """Contiguous (start, end, replacement) hunks, 1-based inclusive on `old`."""
    a, b = old.splitlines(keepends=True), new.splitlines(keepends=True)
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if i1 == i2:  # pure insertion: anchor on the previous line
            if i1 == 0:
                continue
            i1 -= 1
            hunks.append((i1 + 1, i1 + 1, "".join(a[i1:i1 + 1]) + "".join(b[j1:j2])))
        else:
            hunks.append((i1 + 1, i2, "".join(b[j1:j2])))
    return hunks


# ---------------------------------------------------------------- ruff ----
def _apply_ruff_edits(source: str, edits: list[dict]) -> str:
    starts = [0]
    for ln in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(ln))

    def off(loc: dict) -> int:
        row, col = loc["row"], loc["column"]
        if row - 1 >= len(starts):
            return len(source)
        return min(starts[row - 1] + max(col - 1, 0), len(source))

    out = source
    for e in sorted(edits, key=lambda e: off(e["location"]), reverse=True):
        out = out[: off(e["location"])] + e["content"] + out[off(e["end_location"]):]
    return out


def run_ruff(files: list[str]) -> list[Finding]:
    files = [f for f in files if f.endswith((".py", ".pyi"))]
    if not files:
        return []
    proc = _run(["ruff", "check", "--output-format", "json", "--no-fix", "--exit-zero", *files])
    if not proc or not proc.stdout.strip():
        return []
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log("ruff: unparsable output")
        return []
    out: list[Finding] = []
    cache: dict[str, str | None] = {}
    for item in raw:
        path = os.path.relpath(item["filename"])
        loc, end = item["location"], item["end_location"]
        tool_fix = None
        fix = item.get("fix")
        start_line, end_line = loc["row"], max(end["row"], loc["row"])
        if fix and fix.get("applicability") == "safe":
            src = cache.setdefault(path, _read(path))
            if src is not None:
                patched = _apply_ruff_edits(src, fix["edits"])
                hunks = _changed_region(src, patched)
                if len(hunks) == 1:
                    start_line, end_line, tool_fix = hunks[0]
        out.append(Finding(
            tool="ruff", rule=item["code"] or "ruff", path=path, line=start_line, end_line=end_line,
            message=item["message"], severity=Severity.warning, tool_fix=tool_fix,
        ))
    return out


# ------------------------------------------------------------- vulture ----
_VULTURE = re.compile(r"^(?P<p>.+?):(?P<l>\d+): (?P<m>.+) \((?P<c>\d+)% confidence\)$")


def run_vulture(files: list[str]) -> list[Finding]:
    files = [f for f in files if f.endswith(".py")]
    if not files:
        return []
    proc = _run(["vulture", *files, "--min-confidence", "80"])
    out: list[Finding] = []
    for ln in (proc.stdout.splitlines() if proc else []):
        m = _VULTURE.match(ln.strip())
        if m:
            out.append(Finding(tool="vulture", rule="dead-code", path=os.path.relpath(m["p"]), line=int(m["l"]),
                               end_line=int(m["l"]), message=f"{m['m']} ({m['c']}% confidence)", severity=Severity.info))
    return out


# --------------------------------------------------------------- radon ----
def run_radon(files: list[str], min_rank: str = "C") -> list[Finding]:
    files = [f for f in files if f.endswith(".py")]
    if not files:
        return []
    proc = _run(["radon", "cc", "-j", "-n", min_rank, *files])
    if not proc or not proc.stdout.strip():
        return []
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    out: list[Finding] = []
    for path, blocks in raw.items():
        if not isinstance(blocks, list):
            continue
        for b in blocks:
            out.append(Finding(tool="radon", rule=f"cc-{b['rank']}", path=os.path.relpath(path), line=b["lineno"],
                               end_line=b.get("endline", b["lineno"]),
                               message=f"{b['name']}: cyclomatic complexity {b['complexity']} (rank {b['rank']})",
                               severity=Severity.info))
    return out


# --------------------------------------------------------- shellcheck ----
_SC_LEVEL = {"error": Severity.error, "warning": Severity.warning}


def run_shellcheck(files: list[str]) -> list[Finding]:
    files = [f for f in files if f.endswith((".sh", ".bash"))]
    if not files:
        return []
    proc = _run(["shellcheck", "-f", "json1", *files])
    if not proc or not proc.stdout.strip():
        return []
    try:
        comments = json.loads(proc.stdout).get("comments", [])
    except json.JSONDecodeError:
        return []
    return [Finding(tool="shellcheck", rule=f"SC{c['code']}", path=os.path.relpath(c["file"]), line=c["line"],
                    end_line=c.get("endLine", c["line"]), message=c["message"],
                    severity=_SC_LEVEL.get(c["level"], Severity.info)) for c in comments]


# -------------------------------------------------------------- zizmor ----
_ZSEV = {"High": Severity.error, "Medium": Severity.warning}


def run_zizmor(files: list[str]) -> list[Finding]:
    wf = [f for f in files if re.search(r"(^|/)\.github/(workflows|actions)/.+\.ya?ml$", f)]
    if not wf:
        return []
    proc = _run(["zizmor", "--offline", "--no-progress", "--format", "json-v1", *wf])
    if not proc or not proc.stdout.strip():
        return []
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    out: list[Finding] = []
    for r in raw:
        if r.get("ignored"):
            continue
        sev = _ZSEV.get(r.get("determinations", {}).get("severity", ""), Severity.info)
        for loc in r.get("locations", []):
            if loc.get("symbolic", {}).get("kind") != "Primary":
                continue
            key = loc["symbolic"]["key"].get("Local", {}).get("verbatim_path")
            pt = loc["concrete"]["location"]
            if not key:
                continue
            out.append(Finding(tool="zizmor", rule=r["ident"], path=os.path.relpath(key), line=pt["start_point"]["row"] + 1,
                               end_line=pt["end_point"]["row"] + 1,
                               message=f"{r['desc']}: {loc['symbolic'].get('annotation', '')}".strip(": "), severity=sev))
    return out


def zizmor_autofixes(files: list[str]) -> list[Finding]:
    """Apply zizmor's auto-fixes to a scratch copy and turn the delta into suggestions.

    zizmor 1.30.1 marks every fix it offers as `unsafe` (verified against its
    json-v1 output), so `--fix=safe` applies nothing at all. We therefore run
    `--fix=all` on a throwaway copy: the real workflow file is never changed
    here, and every resulting hunk still has to pass the diff-visibility clamp
    and the YAML patch gate before it can be posted as a suggestion.
    """
    wf = [f for f in files if re.search(r"(^|/)\.github/(workflows|actions)/.+\.ya?ml$", f)]
    out: list[Finding] = []
    if not wf or not shutil.which("zizmor"):
        return out
    with tempfile.TemporaryDirectory() as td:
        copies: list[str] = []
        for f in wf:
            src = _read(f)
            if src is None:
                continue
            dst = os.path.join(td, f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(src)
            copies.append(dst)
        if not copies:
            return out
        _run(["zizmor", "--offline", "--no-progress", "--fix=all", "--format", "plain", *copies], timeout=60)
        for f in wf:
            old, dst = _read(f), os.path.join(td, f)
            new = _read(dst) if os.path.exists(dst) else None
            if old is None or new is None or old == new:
                continue
            for s, e, rep in _changed_region(old, new):
                out.append(Finding(tool="zizmor", rule="zizmor-autofix", path=f, line=s, end_line=e,
                                   message="zizmor auto-fix (scratch copy, review before applying)",
                                   severity=Severity.warning, tool_fix=rep))
    return out


# -------------------------------------------------------------- trivy -----
def run_trivy_sarif(sarif_path: str) -> list[Finding]:
    """Parse an existing Trivy SARIF file (the scan itself runs in a separate,
    pinned step because of the vulnerability DB download)."""
    try:
        with open(sarif_path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    out: list[Finding] = []
    for run in doc.get("runs", []):
        for res in run.get("results", []):
            for loc in res.get("locations", []):
                phys = loc.get("physicalLocation", {})
                uri = phys.get("artifactLocation", {}).get("uri")
                reg = phys.get("region", {})
                if uri:
                    out.append(Finding(tool="trivy", rule=res.get("ruleId", "trivy"), path=uri,
                                       line=max(reg.get("startLine", 1), 1), end_line=max(reg.get("endLine", 1), 1),
                                       message=res.get("message", {}).get("text", "")[:240],
                                       severity=Severity.error if res.get("level") == "error" else Severity.warning))
    return out


def collect(files: list[str], profile: str = "balanced") -> list[Finding]:
    """Run all scanners for the profile on the given changed files."""
    out: list[Finding] = []
    out += run_ruff(files)
    out += run_shellcheck(files)
    out += run_zizmor(files)
    out += zizmor_autofixes(files)
    if profile in ("balanced", "deep"):
        out += run_vulture(files)
        out += run_radon(files)
    return out
