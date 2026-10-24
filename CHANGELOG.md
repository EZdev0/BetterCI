# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [Unreleased] — 2026-10-24

### Added

- `.github/workflows/ci.yml`: a real CI pipeline with a Python 3.11/3.12/3.13 test matrix and a lint job (Ruff, `bash -n` + ShellCheck, zizmor, actionlint). SHA-pinned actions, least-privilege permissions, timeouts, concurrency and a deterministic self-scan of the action.
- `.github/dependabot.yml` for weekly action-pin updates with a 7-day cooldown.
- `requirements-dev.in` / `requirements-dev.lock`: hash-locked pytest dependencies.
- `pyproject.toml` with an explicit Ruff rule set, so lint results do not depend on Ruff's changing defaults.
- `tests/test_regressions.py` and `tests/test_repo_integrity.py` (32 tests total).
- `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` and `.gitignore`.
- English-only documentation; `docs/RESEARCH.md` translated from German.

### Fixed

- `scan` crashed on a root commit or shallow clone because `HEAD~1` did not exist; missing base refs now fall back to a diff against the empty tree.
- `parse_diff` mistook an added `+++ ` source line inside a hunk for a `+++ b/...` file header.
- The patch gate compared the total number of Ruff findings, so a patch could hide a new error by fixing an unrelated one. No-regression is now checked per rule code, and ShellCheck findings are checked for shell files.
- `zizmor --fix=safe` applied no fixes at all with zizmor 1.30.1 (all fixes are marked `unsafe`); autofixes now run with `--fix=all` on a scratch copy, leaving the original workflow untouched.
- `scripts/install-llama.sh` trusted cached extracted binaries; the archive is now re-hashed on every run and the extracted build is rebuilt from it.
- PR-controlled text could escape the old `<code>` prompt wrapper; prompts now use a per-request random untrusted-data marker.
- `cmd_post` crashed when no `review.json` existed.
- The README development command was invalid under `--require-hashes`.

### Changed

- All documentation is now English; the README example uses a real commit SHA instead of a placeholder and no longer claims a non-existent benchmark workflow.
- CI jobs pin `ubuntu-24.04` because GitHub started migrating `ubuntu-latest` to Ubuntu 26 in October 2026.

## [0.1.0] — 2026-10-13

Initial alpha: composite action, deterministic scanners, schema-constrained local model, patch gate and GitHub suggestion posting.
