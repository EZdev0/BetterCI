"""Command line: scan | post | doctor | schema."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Dict, List, Set

from . import __version__, diffmap, tools
from .llm import LLMClient
from .models import Finding, Suggestion, fix_schema
from .review import build_suggestions, post_review
from .tools import log


def _esc(s: str, prop: bool = False) -> str:
    s = s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return s.replace(":", "%3A").replace(",", "%2C") if prop else s


def _in_diff(f: Finding, dm: Dict[str, Set[int]]) -> bool:
    vis = dm.get(f.path, set())
    return any(n in vis for n in range(f.line, f.end_line + 1))


def cmd_scan(a: argparse.Namespace) -> int:
    t0 = time.monotonic()
    base = a.base or (f"origin/{os.environ['GITHUB_BASE_REF']}" if os.environ.get("GITHUB_BASE_REF") else "HEAD~1")
    dm = diffmap.parse_diff(diffmap.git_diff(base, a.head))
    files = [p for p in diffmap.changed_files(dm) if os.path.isfile(p)]
    log(f"{len(files)} changed file(s) vs {base}")
    findings = [f for f in tools.collect(files, a.profile) if _in_diff(f, dm)]
    log(f"{len(findings)} finding(s) in changed lines ({time.monotonic() - t0:.1f}s)")

    client = None
    if a.llm_url and not a.no_llm:
        try:
            client = LLMClient(a.llm_url, a.transport, a.model, timeout=a.llm_timeout)
        except Exception as exc:  # noqa: BLE001
            log(f"llm disabled: {exc}")
    sugs = build_suggestions(findings, dm, client, a.max_llm, a.llm_budget)

    os.makedirs(a.out, exist_ok=True)
    covered = {(s.path, n) for s in sugs for n in range(s.start_line, s.end_line + 1)}
    rest = [f for f in findings if not any((f.path, n) in covered for n in range(f.line, f.end_line + 1))]
    with open(os.path.join(a.out, "review.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": __version__, "suggestions": [s.model_dump() for s in sugs]}, fh, indent=2)
    with open(os.path.join(a.out, "findings.json"), "w", encoding="utf-8") as fh:
        json.dump([f.model_dump(mode="json") for f in findings], fh, indent=2)

    for f in rest[:50]:
        lvl = "error" if f.severity.value == "error" else "warning" if f.severity.value == "warning" else "notice"
        print(f"::{lvl} file={_esc(f.path, True)},line={f.line},endLine={f.end_line},title={_esc('VibeCI ' + f.rule, True)}::{_esc(f.message)}")
    summary = (f"### VibeCI\n- findings in changed lines: **{len(findings)}**\n- one-click suggestions: **{len(sugs)}** "
               f"({sum(s.source == 'tool' for s in sugs)} deterministic, {sum(s.source == 'llm' for s in sugs)} local model)\n"
               f"- runtime: {time.monotonic() - t0:.1f}s\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(summary)
    print(summary)
    return 1 if a.fail_on == "error" and any(f.severity.value == "error" for f in rest) else 0


def cmd_post(a: argparse.Namespace) -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
            ev = json.load(fh)
        pr, sha = ev["pull_request"]["number"], ev["pull_request"]["head"]["sha"]
        fork = ev["pull_request"]["head"]["repo"]["full_name"] != os.environ["GITHUB_REPOSITORY"]
    except (KeyError, OSError, json.JSONDecodeError):
        log("not a pull_request event; nothing to post")
        return 0
    if fork:
        log("fork PR: token is read-only, suggestions stay in the job summary/annotations")
        return 0
    with open(os.path.join(a.out, "review.json"), encoding="utf-8") as fh:
        sugs = [Suggestion.model_validate(s) for s in json.load(fh)["suggestions"]]
    log(post_review(os.environ["GITHUB_REPOSITORY"], pr, sha, sugs, token) if sugs and token else "no suggestions or no token")
    return 0


def cmd_doctor(a: argparse.Namespace) -> int:
    lines = LLMClient(a.llm_url, "grammar", a.model).doctor()
    print("\n".join(lines))
    return 1 if any(ln.startswith("FAIL") for ln in lines) else 0


def cmd_schema(a: argparse.Namespace) -> int:
    print(json.dumps(fix_schema(), indent=2))
    return 0


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vibeci")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--base"), s.add_argument("--head", default="HEAD")
    s.add_argument("--profile", choices=["fast", "balanced", "deep"], default="balanced")
    s.add_argument("--llm-url", default=""), s.add_argument("--no-llm", action="store_true")
    s.add_argument("--transport", choices=["anthropic", "grammar"], default="anthropic")
    s.add_argument("--model", default="local"), s.add_argument("--max-llm", type=int, default=5)
    s.add_argument("--llm-budget", type=float, default=150.0), s.add_argument("--llm-timeout", type=float, default=120.0)
    s.add_argument("--out", default="vibeci-out"), s.add_argument("--fail-on", choices=["none", "error"], default="none")
    s.set_defaults(fn=cmd_scan)
    q = sub.add_parser("post"); q.add_argument("--out", default="vibeci-out"); q.set_defaults(fn=cmd_post)
    d = sub.add_parser("doctor"); d.add_argument("--llm-url", default="http://127.0.0.1:8080")
    d.add_argument("--model", default="local"); d.set_defaults(fn=cmd_doctor)
    sub.add_parser("schema").set_defaults(fn=cmd_schema)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
