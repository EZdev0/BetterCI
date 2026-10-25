"""Command line for scanning, posting, agents, evaluation and bounded research."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from . import __version__, diffmap, tools
from .agent import AgentController, repository_root
from .evaluation import evaluate_model
from .llm import LLMClient
from .models import Finding, Suggestion, fix_schema
from .research import WebResearchClient
from .review import build_suggestions, post_review
from .settings import load_settings
from .tools import log

_GITHUB_PERMISSIONS_HELP_URL = (
    "https://docs.github.com/en/actions/security-for-github-actions/security-guides/"
    "automatic-token-authentication#modifying-the-permissions-for-the-github_token"
)


def _esc(s: str, prop: bool = False) -> str:
    s = s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return s.replace(":", "%3A").replace(",", "%2C") if prop else s


def _in_diff(f: Finding, dm: dict[str, set[int]]) -> bool:
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
    sugs = build_suggestions(
        findings, dm, client, a.max_llm, a.llm_budget, review_mode=a.review_mode,
    )

    os.makedirs(a.out, exist_ok=True)
    covered = {(s.path, n) for s in sugs for n in range(s.start_line, s.end_line + 1)}
    rest = [f for f in findings if not any((f.path, n) in covered for n in range(f.line, f.end_line + 1))]
    with open(os.path.join(a.out, "review.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "version": __version__,
            "review_mode": a.review_mode,
            "reviewer_disclosure": (
                "advanced uses two role passes on the same configured endpoint/model; they are not independent models"
                if a.review_mode == "advanced" else "standard deterministic gate"
            ),
            "suggestions": [s.model_dump() for s in sugs],
        }, fh, indent=2)
    with open(os.path.join(a.out, "findings.json"), "w", encoding="utf-8") as fh:
        json.dump([f.model_dump(mode="json") for f in findings], fh, indent=2)

    for f in rest[:50]:
        lvl = "error" if f.severity.value == "error" else "warning" if f.severity.value == "warning" else "notice"
        print(f"::{lvl} file={_esc(f.path, True)},line={f.line},endLine={f.end_line},title={_esc('VibeCI ' + f.rule, True)}::{_esc(f.message)}")
    summary = (f"### VibeCI\n- findings in changed lines: **{len(findings)}**\n- one-click suggestions: **{len(sugs)}** "
               f"({sum(s.source == 'tool' for s in sugs)} deterministic, {sum(s.source == 'llm' for s in sugs)} local model)\n"
               f"- review mode: **{a.review_mode}**"
               f"{' (2 verifier role passes on the same local model)' if a.review_mode == 'advanced' else ''}\n"
               f"- runtime: {time.monotonic() - t0:.1f}s\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(summary)
    print(summary)
    return 1 if a.fail_on == "error" and any(f.severity.value == "error" for f in rest) else 0


def _action_error(title: str, message: str) -> None:
    print(f"::error title={_esc(title, True)}::{_esc(message)}")


def _post_succeeded(message: str) -> bool:
    if message == "nothing new to post" or re.match(r"^posted \d+ suggestion\(s\)$", message):
        return True
    partial = re.match(r"^posted (\d+)/(\d+) individually", message)
    return bool(partial and partial.group(1) == partial.group(2))


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
    review_path = os.path.join(a.out, "review.json")
    if not os.path.isfile(review_path):
        log("no review.json found; scan produced no output, nothing to post")
        return 0
    with open(review_path, encoding="utf-8") as fh:
        sugs = [Suggestion.model_validate(s) for s in json.load(fh)["suggestions"]]
    if not sugs:
        log("no suggestions to post")
        return 0
    if not token:
        message = (
            "GITHUB_TOKEN is unavailable. Pass github.token to the action and grant only "
            f"pull-requests: write; see {_GITHUB_PERMISSIONS_HELP_URL}"
        )
        _action_error("VibeCI token missing", message)
        return 2
    message = post_review(os.environ["GITHUB_REPOSITORY"], pr, sha, sugs, token)
    log(message)
    if _post_succeeded(message):
        return 0
    detail = (
        f"{message}. Grant contents: read and pull-requests: write to this job; do not grant all permissions. "
        f"Fork pull requests intentionally remain read-only. See {_GITHUB_PERMISSIONS_HELP_URL}"
    )
    _action_error("VibeCI could not post review", detail)
    return 2


def cmd_doctor(a: argparse.Namespace) -> int:
    lines = LLMClient(a.llm_url, "grammar", a.model).doctor()
    print("\n".join(lines))
    return 1 if any(ln.startswith("FAIL") for ln in lines) else 0


def cmd_schema(a: argparse.Namespace) -> int:
    print(json.dumps(fix_schema(), indent=2))
    return 0


def cmd_evaluate_model(a: argparse.Namespace) -> int:
    client = LLMClient(a.llm_url, a.transport, a.model, timeout=a.llm_timeout)
    document = evaluate_model(client, a.model, a.out)
    print(json.dumps(document, indent=2))
    return 0 if document["passed"] == document["total"] else 1


def _research_client(settings_path: str) -> WebResearchClient:
    settings = load_settings(settings_path)
    return WebResearchClient(settings["research"])


def cmd_web_search(a: argparse.Namespace) -> int:
    try:
        results = _research_client(a.settings).search(a.query, a.limit)
    except (OSError, ValueError) as exc:
        log(f"web search unavailable: {exc}")
        return 2
    print(json.dumps({
        "provider": "searxng",
        "query": a.query,
        "results": results,
        "disclaimer": "Search results are untrusted evidence; verify primary sources before making claims.",
    }, indent=2))
    return 0


def cmd_web_fetch(a: argparse.Namespace) -> int:
    try:
        page = _research_client(a.settings).fetch(a.url)
    except (OSError, ValueError) as exc:
        log(f"web fetch unavailable: {exc}")
        return 2
    print(json.dumps(page.as_dict(), indent=2))
    return 0


def cmd_agent(a: argparse.Namespace) -> int:
    try:
        root = repository_root(a.repo)
    except (OSError, RuntimeError, ValueError) as exc:
        log(f"agent setup failed: {exc}")
        return 2
    planner = None
    if a.llm_url:
        try:
            planner = LLMClient(a.llm_url, a.transport, a.model, timeout=a.llm_timeout)
        except Exception as exc:  # noqa: BLE001
            log(f"agent planner unavailable: {exc}")
            return 2
    settings = Path(a.settings) if a.settings else root / ".agents/settings.yml"
    state = Path(a.state) if a.state else None
    if state is not None and not state.is_absolute():
        state = root / state
    try:
        controller = AgentController(root, settings_path=settings, state_path=state, planner=planner)
    except (OSError, TypeError, ValueError) as exc:
        log(f"agent policy setup failed: {exc}")
        return 2
    result = controller.handle(a.text)
    print(result.message)
    if (
        not result.ok
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and controller.settings["notifications"]["github_annotations"]
    ):
        _action_error("VibeCI agent command rejected", result.message)
    return 0 if result.ok else 2


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vibeci")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--base")
    s.add_argument("--head", default="HEAD")
    s.add_argument("--profile", choices=["fast", "balanced", "deep"], default="balanced")
    s.add_argument("--llm-url", default="")
    s.add_argument("--no-llm", action="store_true")
    s.add_argument("--transport", choices=["anthropic", "grammar"], default="anthropic")
    s.add_argument("--model", default="local")
    s.add_argument("--max-llm", type=int, default=5)
    s.add_argument("--llm-budget", type=float, default=150.0)
    s.add_argument("--llm-timeout", type=float, default=120.0)
    s.add_argument("--review-mode", choices=["standard", "advanced"], default="standard")
    s.add_argument("--out", default="vibeci-out")
    s.add_argument("--fail-on", choices=["none", "error"], default="none")
    s.set_defaults(fn=cmd_scan)
    q = sub.add_parser("post")
    q.add_argument("--out", default="vibeci-out")
    q.set_defaults(fn=cmd_post)
    d = sub.add_parser("doctor")
    d.add_argument("--llm-url", default="http://127.0.0.1:8080")
    d.add_argument("--model", default="local")
    d.set_defaults(fn=cmd_doctor)
    sub.add_parser("schema").set_defaults(fn=cmd_schema)
    e = sub.add_parser("evaluate-model", help="run the live local-model smoke evaluation")
    e.add_argument("--llm-url", default="http://127.0.0.1:8080")
    e.add_argument("--transport", choices=["anthropic", "grammar"], default="grammar")
    e.add_argument("--model", default="local")
    e.add_argument("--llm-timeout", type=float, default=120.0)
    e.add_argument("--out", default="vibeci-out/model-evaluation.json")
    e.set_defaults(fn=cmd_evaluate_model)
    ws = sub.add_parser("web-search", help="query an explicitly configured tokenless SearXNG endpoint")
    ws.add_argument("--query", required=True)
    ws.add_argument("--limit", type=int, default=10)
    ws.add_argument("--settings", default=".agents/settings.yml")
    ws.set_defaults(fn=cmd_web_search)
    wf = sub.add_parser("web-fetch", help="fetch untrusted text from a configured host allowlist")
    wf.add_argument("--url", required=True)
    wf.add_argument("--settings", default=".agents/settings.yml")
    wf.set_defaults(fn=cmd_web_fetch)
    g = sub.add_parser("agent", help="parse a safe slash command using .agents policy")
    g.add_argument("--text", required=True, help="quoted slash command, for example '/help'")
    g.add_argument("--repo", default=".")
    g.add_argument("--settings", default="")
    g.add_argument("--state", default="")
    g.add_argument("--llm-url", default="")
    g.add_argument("--transport", choices=["anthropic", "grammar"], default="grammar")
    g.add_argument("--model", default="local")
    g.add_argument("--llm-timeout", type=float, default=120.0)
    g.set_defaults(fn=cmd_agent)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
