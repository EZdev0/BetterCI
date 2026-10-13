# VibeCI

**Deterministic-first CI triage with a local 7B model. One-click fixes as native GitHub suggestions. No API keys. Code never leaves the runner.**

![status](https://img.shields.io/badge/status-v0.1%20alpha-orange) ![license](https://img.shields.io/badge/license-MIT-blue) ![runner](https://img.shields.io/badge/runner-ubuntu--latest-lightgrey) ![keys](https://img.shields.io/badge/API%20keys-0-brightgreen)

## Why
AI reviewers fail in CI for three reasons: they spam, they hallucinate broken patches, and they need a cloud key. VibeCI inverts the order: 

1. **Scanners find, the model only fixes.** Ruff, ShellCheck, zizmor, Vulture, Radon decide *what* is wrong. Deterministic fixes (Ruff, zizmor `--fix=safe`) are posted without any model.
2. **The model is boxed in.** It sees one finding plus a numbered window, answers only through a forced schema (Anthropic SDK tool-use or llama.cpp `json_schema` grammar), and has no tools.
3. **Nothing is posted unverified.** The patched file must parse, must not add linter or compiler errors, and must touch only lines visible in the PR diff.

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
      - uses: EZdev0/BetterCI@<full-commit-sha-of-the-release-you-reviewed>
```
Pin the action to a **full commit SHA**. Mutable tags are exactly what broke Trivy's action in March 2026.

Fork PRs: GitHub gives `pull_request` runs a read-only token, so suggestions appear as annotations and in the job summary. Posting for forks needs a separate `workflow_run` job (see `docs/ARCHITECTURE.md`).

## Inputs
| input | default | meaning |
|---|---|---|
| `profile` | `balanced` | `fast` (Ruff, ShellCheck, zizmor) or `balanced` (+ Vulture, Radon) |
| `llm` | `true` | use the local model for findings scanners cannot fix |
| `transport` | `anthropic` | `anthropic` = Anthropic SDK -> llama-server `/v1/messages`; `grammar` = `/v1/chat/completions` + `json_schema` |
| `max-llm-findings` / `llm-budget-seconds` | `5` / `150` | hard caps on model work |
| `spec-type` | `none` | llama.cpp speculative decoding, e.g. `ngram-mod`. Benchmark before enabling |
| `verify` | `full` | re-hash the cached model each run, or `size` |
| `fail-on` | `none` | `error` fails the job on unfixed error findings |

## Language matrix
| language | scanners | patch gate |
|---|---|---|
| Python | Ruff, Vulture, Radon | `ast` + `compile` + Ruff no-regression |
| Shell | ShellCheck | `bash -n` |
| GitHub workflows | zizmor (+ `--fix=safe`) | YAML parse |
| C/C++ | gate only in v0.1 (cppcheck/clang-tidy parsers planned) | `gcc/g++ -fsyntax-only`, no new errors |

## Honest performance statement
Targets are **measured, not claimed**. Run the `benchmark` workflow (cold, then cached) on your runner and read the job summary. A 7B Q4 model on 4 vCPUs prefills only tens of tokens per second, so the model stage is budgeted and the deterministic stage is what keeps typical runs short. Results table: *pending first benchmark run*.

## Comparison (capabilities, not speed)
| | VibeCI | hosted SAST/AI reviewers |
|---|---|---|
| Needs API key / external service | no | usually yes |
| Code leaves the runner | no (loopback only) | usually yes |
| Native `suggestion` blocks | yes | varies |
| Model output validated by compiler/linter | yes | varies |

## Develop
```bash
python3 -m venv .venv && .venv/bin/pip install --require-hashes --no-deps -r requirements.lock pytest
PYTHONPATH=. .venv/bin/python -m pytest -q tests
```
