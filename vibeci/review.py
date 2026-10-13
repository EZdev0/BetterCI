"""Turn validated proposals into GitHub review comments (native ```suggestion
blocks) and post them. Posting never fails the build."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Set

from .diffmap import contiguous
from .gate import apply_replacement, check_patched
from .llm import LLMClient
from .models import Finding, Severity, Suggestion
from .tools import _read, log

_SEV_RANK = {Severity.error: 0, Severity.warning: 1, Severity.info: 2}
# Findings worth spending LLM time on (the rest only get annotations).
LLM_RULES = re.compile(r"^(SIM115|B\d+|S\d+|PLW\d+|SC20\d\d|SC2\d{3}|cc-[C-F]|template-injection|artipacked|excessive-permissions|dangerous-triggers)$")


def _overlaps(a: Suggestion, b: Suggestion) -> bool:
    return a.path == b.path and not (a.end_line < b.start_line or b.end_line < a.start_line)


def _validated(path: str, source: str, start: int, end: int, rep: str, visible: Set[int]) -> Optional[str]:
    if not contiguous(visible, start, end):
        return "lines not in diff"
    try:
        patched = apply_replacement(source, start, end, rep)
    except ValueError as exc:
        return str(exc)
    res = check_patched(path, source, patched)
    return None if res.ok else res.reason


def build_suggestions(findings: List[Finding], diff_map: Dict[str, Set[int]], client: Optional[LLMClient],
                      max_llm: int, budget_s: float, max_total: int = 20) -> List[Suggestion]:
    out: List[Suggestion] = []
    findings = sorted(findings, key=lambda f: (_SEV_RANK[f.severity], f.path, f.line))
    # 1) deterministic fixes first: zero hallucination risk, zero LLM time
    for f in findings:
        visible = diff_map.get(f.path, set())
        src = _read(f.path)
        if f.tool_fix is None or src is None:
            continue
        why = _validated(f.path, src, f.line, f.end_line, f.tool_fix, visible)
        if why:
            log(f"drop tool fix {f.tool}/{f.rule} {f.path}:{f.line}: {why}")
            continue
        s = Suggestion(path=f.path, start_line=f.line, end_line=f.end_line, replacement=f.tool_fix,
                       title=f.message, detail=f"Deterministic auto-fix from `{f.tool}` ({f.rule}).",
                       source="tool", rule=f.rule, confidence="high")
        if not any(_overlaps(s, o) for o in out):
            out.append(s)
    # 2) LLM proposals, only for unresolved findings inside the diff, hard-budgeted
    if client is not None and max_llm > 0:
        deadline = time.monotonic() + budget_s
        spent = 0
        for f in findings:
            if spent >= max_llm or len(out) >= max_total or time.monotonic() > deadline:
                break
            visible = diff_map.get(f.path, set())
            if f.tool_fix is not None or not LLM_RULES.match(f.rule) or not contiguous(visible, f.line, f.end_line):
                continue
            if any(o.path == f.path and o.start_line <= f.line <= o.end_line for o in out):
                continue
            src = _read(f.path)
            if src is None:
                continue
            spent += 1
            got = client.propose(f, src, deadline)
            if not got or got[0].verdict != "fix":
                continue
            prop = got[0]
            why = _validated(f.path, src, prop.start_line, prop.end_line, prop.replacement or "", visible)
            if why:
                log(f"drop llm fix {f.rule} {f.path}:{prop.start_line}: {why}")
                continue
            detail = prop.bug
            if prop.edge_case:
                detail += f"\n\n**What-if ({prop.edge_case.kind}):** {prop.edge_case.scenario}"
            s = Suggestion(path=f.path, start_line=prop.start_line, end_line=prop.end_line,
                           replacement=prop.replacement or "", title=f.message, detail=detail,
                           source="llm", rule=f.rule, confidence=prop.confidence)
            if not any(_overlaps(s, o) for o in out):
                out.append(s)
    return out[:max_total]


# ------------------------------------------------------------ rendering ---
def _clean(text: str, limit: int = 600) -> str:
    text = re.sub(r"<[^>]*>", "", text)           # no HTML
    text = text.replace("@", "@\u200b")           # no mentions
    return text.strip()[:limit]


def render_body(s: Suggestion) -> str:
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", s.replacement)), default=0) + 1)
    rep = s.replacement if s.replacement.endswith("\n") or s.replacement == "" else s.replacement + "\n"
    badge = "🔧 auto-fix" if s.source == "tool" else f"🤖 local model · confidence {s.confidence}"
    return (f"**VibeCI · `{s.rule}`** · {badge}\n\n{_clean(s.title, 200)}\n\n{_clean(s.detail)}\n\n"
            f"{fence}suggestion\n{rep}{fence}\n<!-- vibeci:fp={s.fingerprint} -->")


def to_comments(sugs: List[Suggestion]) -> List[dict]:
    comments = []
    for s in sugs:
        c = {"path": s.path, "line": s.end_line, "side": "RIGHT", "body": render_body(s)}
        if s.end_line > s.start_line:
            c.update({"start_line": s.start_line, "start_side": "RIGHT"})
        comments.append(c)
    return comments


# -------------------------------------------------------------- posting ---
def _api(method: str, url: str, token: str, payload: Optional[dict] = None) -> tuple:
    req = urllib.request.Request(url, method=method, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - fixed api.github.com host
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}
    except urllib.error.URLError as e:
        return 0, {"error": str(e)}


def existing_fingerprints(repo: str, pr: int, token: str) -> Set[str]:
    seen: Set[str] = set()
    for page in range(1, 6):
        code, data = _api("GET", f"https://api.github.com/repos/{repo}/pulls/{pr}/comments?per_page=100&page={page}", token)
        if code != 200 or not isinstance(data, list) or not data:
            break
        for c in data:
            seen.update(re.findall(r"vibeci:fp=([0-9a-f]{16})", c.get("body", "")))
    return seen


def post_review(repo: str, pr: int, sha: str, sugs: List[Suggestion], token: str) -> str:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return "invalid repo"
    seen = existing_fingerprints(repo, pr, token)
    fresh = [s for s in sugs if s.fingerprint not in seen]
    if not fresh:
        return "nothing new to post"
    body = f"VibeCI found {len(fresh)} one-click fix(es). Each is a native suggestion: *Add suggestion to batch*."
    code, data = _api("POST", f"https://api.github.com/repos/{repo}/pulls/{pr}/reviews", token,
                      {"commit_id": sha, "event": "COMMENT", "body": body, "comments": to_comments(fresh)})
    if code in (200, 201):
        return f"posted {len(fresh)} suggestion(s)"
    if code == 403 or code == 404:
        return f"no write access (fork PR or missing permission): HTTP {code}"
    posted = 0  # a single bad line anchor makes the whole review 422: retry one by one
    for s in fresh:
        c, _ = _api("POST", f"https://api.github.com/repos/{repo}/pulls/{pr}/comments", token,
                    {**to_comments([s])[0], "commit_id": sha})
        posted += c in (200, 201)
    return f"posted {posted}/{len(fresh)} individually after HTTP {code}"
