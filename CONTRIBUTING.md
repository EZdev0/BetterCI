# Contributing

All documentation, comments and commit messages in this repository are in **English**.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements.lock -r requirements-dev.lock
PYTHONPATH=. .venv/bin/python -m pytest -q tests
```

`requirements.lock` holds the runtime tools, `requirements-dev.lock` the test dependencies. Both are hash-locked; never add an unhashed package to a CI install. Regenerate the dev lock with the command recorded in its header (uv `pip compile --generate-hashes`).

## Before opening a pull request

Run the same checks as CI:

```bash
.venv/bin/ruff check .
.venv/bin/shellcheck scripts/*.sh
.venv/bin/zizmor --offline --no-progress --strict-collection --collect=all --format plain \
  .github/workflows .github/dependabot.yml action.yml
PYTHONPATH=. .venv/bin/python -m pytest -q tests
```

CI (`.github/workflows/ci.yml`) runs these on Python 3.11, 3.12 and 3.13, plus a deterministic self-scan of the action.

## Error tracking

Errors are tracked in **Linear** (team `PRO`, project "BetterCI (VibeCI) - CI Hardening & Error Tracking") and mirrored in `docs/ERRORS.md`. For every defect, write down both:

1. **How it happened** – the reproduction or observation and the root cause.
2. **Solution path** – the fix, why it was chosen, and the regression test that keeps it fixed.

Never invent data. If a tracking tool rejects or is missing an entry, record the gap in `docs/ERRORS.md` instead of silently dropping it.

## Rules

- Every `uses:` reference in workflows and in `action.yml` must point to a full 40-character commit SHA with the release version in a trailing comment.
- Workflows start with `permissions: {}`; jobs request only the scopes they need.
- Do not put `${{ ... }}` expressions inside `run:` blocks — pass untrusted values through `env:`.
- Any new binary, model or Python dependency must be pinned by version and SHA-256 in a lock file.
- Schema changes must be regenerated with `python -m vibeci schema > schemas/fix.schema.json`; `tests/test_repo_integrity.py` fails when the committed schema drifts from the Pydantic model.
- Add a regression test for every bug you fix. Tests must not mock the behaviour they assert.
- Do not weaken the patch gate in `vibeci/gate.py`: a suggested patch may never introduce a new lint, ShellCheck or compiler rule.

## Adding a scanner

Implement a function in `vibeci/tools.py` that returns `list[Finding]`, register it in `collect()`, and add a test that parses a real tool output sample. Keep parser failures non-fatal: a broken scanner must yield no findings, never an exception.

## Reporting security issues

See [SECURITY.md](SECURITY.md). Do not open a public issue for an exploitable problem.
