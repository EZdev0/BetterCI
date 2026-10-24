"""Unified-diff parsing. GitHub only accepts inline suggestions on lines that
are part of the PR diff (RIGHT side), so every proposal is clamped to this map."""
from __future__ import annotations

import re
import subprocess

from .tools import log

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_SAFE_REF = re.compile(r"[A-Za-z0-9._/\-~^]{1,200}")
# Git's canonical empty tree. Diffing against it makes every line of `head`
# visible, which is the only sensible behaviour for a root commit or a shallow
# checkout where the base ref is missing.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def parse_diff(diff_text: str) -> dict[str, set[int]]:
    """Return {path: {new-file line numbers visible in the diff}}."""
    out: dict[str, set[int]] = {}
    path: str | None = None
    new_line = 0
    in_hunk = False
    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            path, in_hunk = None, False
        elif raw.startswith("+++ ") and not in_hunk:
            # Only a real file header may change `path`. An ADDED source line
            # whose content starts with "++ " is also rendered as "+++ " by git
            # and would otherwise be mistaken for a new-file header mid-hunk.
            target = raw[4:].strip()
            path = None if target == "/dev/null" else target.removeprefix("b/")
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
            if raw.startswith(("+", " ")):
                out[path].add(new_line)
                new_line += 1
            elif raw.startswith(("-", "\\")):
                continue
            else:
                in_hunk = False
    return {p: s for p, s in out.items() if s}


def _ref_exists(ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode == 0


def git_diff(base: str, head: str = "HEAD", context: int = 3) -> str:
    """Diff of the PR (merge-base semantics, three dots).

    If `base` cannot be resolved (root commit behind `HEAD~1`, shallow clone
    without `origin/<base>`, deleted base branch), fall back to a two-dot diff
    against the empty tree instead of crashing the whole action.
    """
    for ref in (base, head):
        if not _SAFE_REF.fullmatch(ref):
            raise ValueError(f"unsafe git ref: {ref!r}")
    common = ["git", "diff", f"-U{int(context)}", "--no-color", "--no-ext-diff"]
    if _ref_exists(base):
        args = [*common, f"{base}...{head}", "--"]
    else:
        log(f"base ref {base!r} not available; diffing {head} against the empty tree")
        subprocess.run(["git", "hash-object", "-t", "tree", "-w", "/dev/null"],
                       capture_output=True, text=True, check=False)
        args = [*common, _EMPTY_TREE, head, "--"]
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git diff failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def contiguous(lines: set[int], start: int, end: int) -> bool:
    """True when every line in [start, end] is visible in the diff."""
    return all(n in lines for n in range(start, end + 1))


def changed_files(diff_map: dict[str, set[int]]) -> list[str]:
    return sorted(diff_map)
