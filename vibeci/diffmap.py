"""Unified-diff parsing. GitHub only accepts inline suggestions on lines that
are part of the PR diff (RIGHT side), so every proposal is clamped to this map."""
from __future__ import annotations

import re
import subprocess
from typing import Dict, List, Set

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff(diff_text: str) -> Dict[str, Set[int]]:
    """Return {path: {new-file line numbers visible in the diff}}."""
    out: Dict[str, Set[int]] = {}
    path: str | None = None
    new_line = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            path, in_hunk = None, False
        elif raw.startswith("+++ "):
            target = raw[4:].strip()
            path = None if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
            if path is not None:
                out.setdefault(path, set())
        elif raw.startswith("@@") and path is not None:
            m = _HUNK.match(raw)
            if not m:
                in_hunk = False
                continue
            new_line = int(m.group(1))
            in_hunk = True
        elif in_hunk and path is not None:
            if raw.startswith("+"):
                out[path].add(new_line)
                new_line += 1
            elif raw.startswith(" "):
                out[path].add(new_line)
                new_line += 1
            elif raw.startswith("-") or raw.startswith("\\"):
                continue
            else:
                in_hunk = False
    return {p: s for p, s in out.items() if s}


def git_diff(base: str, head: str = "HEAD", context: int = 3) -> str:
    """Diff of the PR (merge-base semantics, three dots)."""
    for ref in (base, head):
        if not re.fullmatch(r"[A-Za-z0-9._/\-~^]{1,200}", ref):
            raise ValueError(f"unsafe git ref: {ref!r}")
    proc = subprocess.run(
        ["git", "diff", f"-U{int(context)}", "--no-color", "--no-ext-diff", f"{base}...{head}"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def contiguous(lines: Set[int], start: int, end: int) -> bool:
    """True when every line in [start, end] is visible in the diff."""
    return all(n in lines for n in range(start, end + 1))


def changed_files(diff_map: Dict[str, Set[int]]) -> List[str]:
    return sorted(diff_map)
