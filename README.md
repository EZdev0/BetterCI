# VibeCI

**Deterministic-first CI triage with an optional local code model. Evidence-backed goals, gated fixes and native GitHub suggestions. No API keys. Code never leaves the runner.**

![CI](https://github.com/EZdev0/BetterCI/actions/workflows/ci.yml/badge.svg) ![status](https://img.shields.io/badge/status-v0.1%20alpha-orange) ![license](https://img.shields.io/badge/license-MIT-blue) ![runner](https://img.shields.io/badge/runner-ubuntu--24.04-lightgrey) ![keys](https://img.shields.io/badge/API%20keys-0-brightgreen)

## Documentation

All documentation in this repository is in English.

* [Agent policy](AGENTS.md) and [`.agents/`](.agents/) – mandatory goal/plan/approval lifecycle, roles, commands and settings.
* [Architecture](docs/ARCHITECTURE.md) – pipelines, trust boundaries, worktrees and restoration.
* [Error register](docs/ERRORS.md) – tracked defects, causes, solution paths and regression tests.
* [Research log](RESEARCH.md) – dated official sources and model decisions; [historical audit](docs/RESEARCH.md).
* [Future development](FUTURE.md), [research prompt](docs/RESEARCH_PROMPT.md) and [90-day roadmap](docs/ROADMAP.md).
* [Contributing](CONTRIBUTING.md), [Security policy](SECURITY.md), [Changelog](CHANGELOG.md).

## Why
AI reviewers fail in CI for three reasons: they spam, they hallucinate broken patches, and they need a cloud key. VibeCI inverts the order:

1. **Scanners find, the model only fixes.** Ruff, ShellCheck, zizmor, Vulture, Radon decide *what* is wrong. Deterministic fixes (Ruff safe edits, zizmor auto-fixes produced on a scratch copy and then gated) are posted without any model.
2. **The model is boxed in.** It sees one finding plus a numbered window inside a per-request random untrusted-data marker, answers only through a forced schema (Anthropic SDK tool-use or llama.cpp `json_schema` grammar), and has no tools.
3. **Nothing is posted unverified.** The patched file must parse, must not add a *new* linter/compiler/ShellCheck rule (compared per rule code, not by total count), must avoid suspicious added code/excessive deletion and must touch only lines visible in the PR diff.
4. **Advanced work starts from evidence.** A goal needs measurable acceptance criteria and path/rule/issue/URL references. A read-only plan gets an immutable digest; writes need `/approve <plan-id>` and a clean baseline.
5. **Workers are recoverable.** Every task attempt gets a dedicated branch/worktree. `/verify` runs deny-first change policy plus reviewed argv checks; `/restore` is restricted to marked worker worktrees and preserves untracked files.

## Install (one workflow file)
```yaml
# .github/workflows/vibeci.yml
name: VibeCI
on: pull_request
permissions: {}
jobs:
  vibeci:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    permissions: { contents: read, pull-requests: write }
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { fetch-depth: 0, persist-credentials: false }
      - uses: EZdev0/BetterCI@e72808021f3586921b44c6b90102c0ea0a966007 # merged CI-hardened baseline
```
The example above is a **real commit SHA** on `main` (not a placeholder). Pin the action to the full commit you actually reviewed and replace it when you tag a release. Mutable tags are exactly what broke Trivy's action in March 2026.

Fork PRs: GitHub gives `pull_request` runs a read-only token, so suggestions appear as annotations and in the job summary. Posting for forks needs a separate `workflow_run` job (see `docs/ARCHITECTURE.md`).

## Inputs
| input | default | meaning |
|---|---|---|
| `profile` | `balanced` | `fast` (Ruff, ShellCheck, zizmor) or `balanced` (+ Vulture, Radon); `deep` currently behaves like `balanced` |
| `llm` | `true` | use the local model for findings scanners cannot fix |
| `model` | `qwen2.5-coder-7b` | pinned quality profile from `tools.lock.json`; `qwen2.5-coder-3b-fast` is the opt-in fast candidate |
| `transport` | `anthropic` | `anthropic` = Anthropic SDK -> llama-server `/v1/messages`; `grammar` = `/v1/chat/completions` + `json_schema` |
| `review-mode` | `standard` | `standard` = deterministic gate; `advanced` = two same-model read-only verifier role passes for each model-generated patch after the gate |
| `max-llm-findings` | `5` | hard cap on findings sent to the model |
| `llm-budget-seconds` | `150` | wall-clock budget for all model calls |
| `threads` | `0` | CPU threads for llama-server (`0` = all cores) |
| `spec-type` | `none` | llama.cpp speculative decoding, e.g. `ngram-mod`. Benchmark before enabling |
| `verify` | `full` | re-hash the cached model each run, or `size` |
| `fail-on` | `none` | `error` fails the job on unfixed error-level findings |
| `post-review` | `true` | post suggestions as a PR review (same-repo PRs only) |

The review bot does **not** need all repository permissions. Same-repository posting needs only `contents: read` (checkout) and `pull-requests: write`; fork PRs intentionally remain read-only. A missing token or HTTP 403/404 now fails the posting step with GitHub's [official `GITHUB_TOKEN` permissions guide](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication#modifying-the-permissions-for-the-github_token) instead of reporting false success.

## Goal-driven agent control (local, advanced)

Every agent must read `AGENTS.md` and `.agents/` first. Mutable state and worktrees stay in ignored `.vibeci/`; policy remains version-controlled.

```bash
# Show the parser contract.
python -m vibeci agent --text "/help"

# Set an evidence-backed goal. Each multi-word option value is quoted.
python -m vibeci agent --text "/goal Harden parser behavior --accept 'All parser tests pass' --ref vibeci/agent.py"

# Planning requires a live loopback model; it cannot fall back to a cloud provider.
python -m vibeci agent --llm-url http://127.0.0.1:8080 --transport grammar --text "/plan"
python -m vibeci agent --text "/status"
python -m vibeci agent --text "/approve <plan-id>"
python -m vibeci agent --text "/start <task-id>"
python -m vibeci agent --text "/verify <task-id>"
# On failure, restore tracked files in that managed worktree only:
python -m vibeci agent --text "/restore <task-id>"

# Inspect managers, record a blocking question, and build (but do not launch) a subagent brief:
python -m vibeci agent --text "/teams"
python -m vibeci agent --text "/ask security -- 'Is this credential flow intentional?' --ref app.py"
python -m vibeci agent --text "/brief security -- 'Review the approved diff'"

# Append a timestamped, evidence-linked local note and inspect bounded shared context:
python -m vibeci agent --text "/note research evidence -- 'JSON output requires instance configuration' --ref RESEARCH.md"
python -m vibeci agent --text "/context"
```

The committed `.agents/bigbrain/` map has one manager per expert team. Mutable notes are immutable per-record files under ignored `.vibeci/bigbrain/`, so teams do not overwrite a shared document. Context is bounded by character/entry budgets; omitted records remain on disk and are represented by a count and SHA-256 digest. Request triage pauses instruction overrides, destructive operations, credential exposure, malware construction and security-bypass requests. A human `/answer` does not bypass policy or approve work.

Scope settings hide configured directories/extensions (including `.eng`) from planner context and reject managed changes to ignored/denied paths. `allowed_path_globs`, `denied_path_globs`, protected paths, verification argv lists, team limits and notification limits are reviewed in `.agents/settings.yml`.

`/approve` never means merge. `/schedule` records only a validated `/verify` or `/status` proposal; it never silently creates workflow YAML. Standard PR findings remain visible through annotations, reviews, summaries and JSON artifacts. Stateful comment execution remains deliberately unshipped until durable state, stronger authorization, low-trust cache handling and ephemeral-runner recovery are solved; see `FUTURE.md`.

### GitHub PR comment diagnostics

The repository ships a separate **read-only** command surface for GitHub PR conversations. It uses ordinary GitHub-hosted Actions runners and requires no local or self-hosted runner. The workflow must first be merged onto the default branch because GitHub dispatches `issue_comment` only when that workflow exists there.

| Command | Result |
|---|---|
| `/help` or `/vibeci help` | Command list, permissions and scope. `/Help` and other casing work too. |
| `/ci` | Current-head workflow and job status, including assigned runner/group/labels and failed steps. |
| `/runs` | Up to five recent runs for the PR's exact current head SHA. |
| `/run <run-id>` | Detailed jobs for one run, rejected if it belongs to another SHA. |
| `/runners` | Runners actually assigned to current-head jobs; no admin-only runner inventory. |
| `/failures` | Failed, cancelled or timed-out jobs and failed step names with GitHub links. |

Only repository `OWNER`, `MEMBER`, and `COLLABORATOR` comments are handled. Responses are bounded to five current-head runs, 50 latest-attempt jobs per run, and a 24,000-character comment budget; larger matrices stay available through the linked GitHub run. The adapter executes default-branch code, never checks out or runs contributor code, never reads job logs, installs no dependencies, uses no cache, and cannot rerun/cancel jobs. Its token is limited to `actions: read`, `contents: read`, `pull-requests: read`, and `issues: write` so it can read diagnostics and post one bounded response. Comment text is parsed from the event JSON as data and never becomes shell input.

### Optional tokenless web research

BetterCI includes a real but **disabled-by-default** SearXNG JSON client and a separate direct text fetcher. It does not choose an unknown public instance, scrape search HTML or claim that a public service is reliable/free forever. Configure a reviewed self-hosted/controlled `research.searxng_url`, enable `research.enabled`, and keep a narrow `fetch_allowed_hosts` list. Requests have time, byte and result caps, reject credential-like query text/parameters, never send an authorization header, do not follow redirects, and return untrusted evidence.

```bash
python -m vibeci web-search --query "site:github.com/example official docs"
python -m vibeci web-fetch --url "https://github.com/example/project"
```

SearXNG must enable its JSON format; many public instances do not. See the dated official-source entries in `RESEARCH.md`. Search/fetch output still requires primary-source verification and is never trusted as a command.

## Language matrix
| language | scanners | patch gate |
|---|---|---|
| Python | Ruff, Vulture, Radon | `ast` + `compile` + per-code Ruff no-regression |
| Shell | ShellCheck | `bash -n` + per-code ShellCheck no-regression |
| GitHub workflows | zizmor (+ scratch-copy auto-fixes) | YAML parse |
| C/C++ | gate only in v0.1 (cppcheck/clang-tidy parsers planned) | `gcc/g++ -fsyntax-only`, no new errors |

## CI in this repository
`.github/workflows/ci.yml` runs on every pull request, on pushes to `main` and `arena/**`, and via `workflow_dispatch`. Two jobs:

* **tests** – Python 3.11/3.12/3.13 matrix. Installs `requirements.lock` + `requirements-dev.lock` with `--require-hashes --no-deps`, runs `pip check`, byte-compiles all modules, runs the full pytest suite (unit tests, mock-llama-server e2e for both transports and advanced reviewers, agent/worktree/policy tests, regression tests, repository-integrity tests), then dogfoods `python -m vibeci scan --no-llm --fail-on error` on its own commit.
* **lint** – Ruff with the explicit rule set in `pyproject.toml`, `bash -n` + ShellCheck on `scripts/*.sh`, zizmor with `--strict-collection` over workflows/`action.yml`/`dependabot.yml`, and actionlint v1.7.12 (release tarball verified against its upstream SHA-256) for workflow syntax and expression types.

`.github/workflows/vibeci-commands.yml` is independent from CI execution. After it is present on the default branch, trusted PR comments can query existing run/job/runner/failure metadata; the workflow cannot start, rerun, cancel, approve, or merge anything.

All `uses:` references are pinned to full commit SHAs and the jobs pin the `ubuntu-24.04` runner label instead of the moving `ubuntu-latest` (GitHub started migrating that label to Ubuntu 26 in October 2026). The workflow starts from `permissions: {}`, grants `contents: read` per job, uses `persist-credentials: false`, sets job timeouts and a concurrency group. `.github/dependabot.yml` proposes pin updates weekly with a 7-day cooldown. The integrity tests fail if a future edit adds an unpinned action, a dangerous trigger, an expression inside a `run:` block, an unhash-locked requirement, or a stale JSON schema.

## Honest performance statement
Targets are **measured, not claimed**. There is no benchmark workflow in this repo yet. To measure the deterministic stage locally:

```bash
/usr/bin/time -f 'deterministic scan: %e s' python -m vibeci scan --no-llm --profile fast --out /tmp/vibeci-bench
```

For the model stage, install the pinned binaries with `scripts/install-llama.sh` + `scripts/fetch-model.sh`, start `scripts/start-server.sh`, and run `python -m vibeci doctor` before timing a scan with `--llm-url http://127.0.0.1:8080`. Compare the pinned candidates with the same runner image:

```bash
python -m vibeci evaluate-model --llm-url http://127.0.0.1:8080 \
  --transport grammar --model local --out vibeci-out/model-evaluation.json
```

The smoke harness records schema/gate/semantic pass results and latency; it explicitly is not a repository-scale correctness benchmark. The 3B Q4_K_M file is 1.93 GB and should be faster to download/infer than 7B, but this has **not** been measured in this repository. The 7B model remains default until cold/warm comparisons are committed. The deterministic stage is what keeps the ordinary path short. Results table: *pending first measured live-model run*.

## Comparison (capabilities, not speed)
| | VibeCI | hosted SAST/AI reviewers |
|---|---|---|
| Default scan needs API key / external service | no | usually yes |
| Code leaves the runner | no for scan/model; opt-in research sends its query/URL to configured hosts | usually yes |
| Native `suggestion` blocks | yes | varies |
| Model output validated by compiler/linter | yes | varies |

## Develop
```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements.lock -r requirements-dev.lock
PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=. .venv/bin/python -m pytest -q tests
```

The old install command (`pip install --require-hashes --no-deps -r requirements.lock pytest`) was broken: in hash-checking mode **every** requirement needs a hash, so an un-hashed `pytest` made pip exit 1. Test dependencies now live in their own hash-locked file. The test command also prepends `.venv/bin` because selecting the venv Python alone does not expose Ruff/Vulture/Radon to child-process discovery. Regenerate the development lock with the command recorded at the top of `requirements-dev.lock`.

Run the same checks as CI locally:

```bash
.venv/bin/ruff check .
.venv/bin/shellcheck scripts/*.sh
.venv/bin/zizmor --offline --no-progress --strict-collection --collect=all --format plain \
  .github/workflows .github/dependabot.yml action.yml
PATH="$PWD/.venv/bin:$PATH" PYTHONPATH=. .venv/bin/python -m pytest -q tests
```
