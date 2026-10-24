# VibeCI

**Deterministic-first CI triage with a local 7B model. One-click fixes as native GitHub suggestions. No API keys. Code never leaves the runner.**

![CI](https://github.com/EZdev0/BetterCI/actions/workflows/ci.yml/badge.svg) ![status](https://img.shields.io/badge/status-v0.1%20alpha-orange) ![license](https://img.shields.io/badge/license-MIT-blue) ![runner](https://img.shields.io/badge/runner-ubuntu--24.04-lightgrey) ![keys](https://img.shields.io/badge/API%20keys-0-brightgreen)

## Documentation

All documentation in this repository is in English.

* [Architecture](docs/ARCHITECTURE.md) – pipeline, trust boundaries, data flow.
* [Error register](docs/ERRORS.md) – tracked defects, root causes, fixes and regression tests.
* [Research dossier](docs/RESEARCH.md) – dated evidence with confidence labels.
* [Research prompt](docs/RESEARCH_PROMPT.md) and [roadmap](docs/ROADMAP.md).
* [Contributing](CONTRIBUTING.md), [Security policy](SECURITY.md), [Changelog](CHANGELOG.md).

## Why
AI reviewers fail in CI for three reasons: they spam, they hallucinate broken patches, and they need a cloud key. VibeCI inverts the order:

1. **Scanners find, the model only fixes.** Ruff, ShellCheck, zizmor, Vulture, Radon decide *what* is wrong. Deterministic fixes (Ruff safe edits, zizmor auto-fixes produced on a scratch copy and then gated) are posted without any model.
2. **The model is boxed in.** It sees one finding plus a numbered window inside a per-request random untrusted-data marker, answers only through a forced schema (Anthropic SDK tool-use or llama.cpp `json_schema` grammar), and has no tools.
3. **Nothing is posted unverified.** The patched file must parse, must not add a *new* linter/compiler/ShellCheck rule (compared per rule code, not by total count), and must touch only lines visible in the PR diff.

## Install (one workflow file)
```yaml
# .github/workflows/vibeci.yml
name: VibeCI
on: pull_request
permissions: {}
jobs:
  vibeci:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    permissions: { contents: read, pull-requests: write }
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with: { fetch-depth: 0, persist-credentials: false }
      - uses: EZdev0/BetterCI@2df287359bf30bc868323ddfc82117dd4114050e # reviewed baseline on main
```
The example above is a **real commit SHA** on `main` (not a placeholder). Pin the action to the full commit you actually reviewed and replace it when you tag a release. Mutable tags are exactly what broke Trivy's action in March 2026.

Fork PRs: GitHub gives `pull_request` runs a read-only token, so suggestions appear as annotations and in the job summary. Posting for forks needs a separate `workflow_run` job (see `docs/ARCHITECTURE.md`).

## Inputs
| input | default | meaning |
|---|---|---|
| `profile` | `balanced` | `fast` (Ruff, ShellCheck, zizmor) or `balanced` (+ Vulture, Radon); `deep` currently behaves like `balanced` |
| `llm` | `true` | use the local model for findings scanners cannot fix |
| `model` | `qwen2.5-coder-7b` | key from `tools.lock.json` |
| `transport` | `anthropic` | `anthropic` = Anthropic SDK -> llama-server `/v1/messages`; `grammar` = `/v1/chat/completions` + `json_schema` |
| `max-llm-findings` | `5` | hard cap on findings sent to the model |
| `llm-budget-seconds` | `150` | wall-clock budget for all model calls |
| `threads` | `0` | CPU threads for llama-server (`0` = all cores) |
| `spec-type` | `none` | llama.cpp speculative decoding, e.g. `ngram-mod`. Benchmark before enabling |
| `verify` | `full` | re-hash the cached model each run, or `size` |
| `fail-on` | `none` | `error` fails the job on unfixed error-level findings |
| `post-review` | `true` | post suggestions as a PR review (same-repo PRs only) |

## Language matrix
| language | scanners | patch gate |
|---|---|---|
| Python | Ruff, Vulture, Radon | `ast` + `compile` + per-code Ruff no-regression |
| Shell | ShellCheck | `bash -n` + per-code ShellCheck no-regression |
| GitHub workflows | zizmor (+ scratch-copy auto-fixes) | YAML parse |
| C/C++ | gate only in v0.1 (cppcheck/clang-tidy parsers planned) | `gcc/g++ -fsyntax-only`, no new errors |

## CI in this repository
`.github/workflows/ci.yml` runs on every pull request, on pushes to `main` and `arena/**`, and via `workflow_dispatch`. Two jobs:

* **tests** – Python 3.11/3.12/3.13 matrix. Installs `requirements.lock` + `requirements-dev.lock` with `--require-hashes --no-deps`, runs `pip check`, byte-compiles all modules, runs the full pytest suite (unit tests, mock-llama-server e2e for both transports, regression tests, repository-integrity tests), then dogfoods `python -m vibeci scan --no-llm --fail-on error` on its own commit.
* **lint** – Ruff with the explicit rule set in `pyproject.toml`, `bash -n` + ShellCheck on `scripts/*.sh`, zizmor with `--strict-collection` over workflows/`action.yml`/`dependabot.yml`, and actionlint v1.7.12 (release tarball verified against its upstream SHA-256) for workflow syntax and expression types.

All `uses:` references are pinned to full commit SHAs and the jobs pin the `ubuntu-24.04` runner label instead of the moving `ubuntu-latest` (GitHub started migrating that label to Ubuntu 26 in October 2026). The workflow starts from `permissions: {}`, grants `contents: read` per job, uses `persist-credentials: false`, sets job timeouts and a concurrency group. `.github/dependabot.yml` proposes pin updates weekly with a 7-day cooldown. The integrity tests fail if a future edit adds an unpinned action, a dangerous trigger, an expression inside a `run:` block, an unhash-locked requirement, or a stale JSON schema.

## Honest performance statement
Targets are **measured, not claimed**. There is no benchmark workflow in this repo yet. To measure the deterministic stage locally:

```bash
/usr/bin/time -f 'deterministic scan: %e s' python -m vibeci scan --no-llm --profile fast --out /tmp/vibeci-bench
```

For the model stage, install the pinned binaries with `scripts/install-llama.sh` + `scripts/fetch-model.sh`, start `scripts/start-server.sh`, and run `python -m vibeci doctor` before timing a scan with `--llm-url http://127.0.0.1:8080`. A 7B Q4 model on 4 vCPUs prefills only tens of tokens per second, so the model stage is budgeted and the deterministic stage is what keeps typical runs short. Results table: *pending first measured run*.

## Comparison (capabilities, not speed)
| | VibeCI | hosted SAST/AI reviewers |
|---|---|---|
| Needs API key / external service | no | usually yes |
| Code leaves the runner | no (loopback only) | usually yes |
| Native `suggestion` blocks | yes | varies |
| Model output validated by compiler/linter | yes | varies |

## Develop
```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements.lock -r requirements-dev.lock
PYTHONPATH=. .venv/bin/python -m pytest -q tests
```

The old command (`pip install --require-hashes --no-deps -r requirements.lock pytest`) was broken: in hash-checking mode **every** requirement needs a hash, so an un-hashed `pytest` made pip exit 1. Test dependencies now live in their own hash-locked file. Regenerate it with the command recorded at the top of `requirements-dev.lock`.

Run the same checks as CI locally:

```bash
.venv/bin/ruff check .
.venv/bin/shellcheck scripts/*.sh
.venv/bin/zizmor --offline --no-progress --strict-collection --collect=all --format plain \
  .github/workflows .github/dependabot.yml action.yml
PYTHONPATH=. .venv/bin/python -m pytest -q tests
```
