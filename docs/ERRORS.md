# Error Register

Tracked defects found during the 2026-10-24 CI audit. Status is derived from code and tests, not from assumptions. This file is the in-repo error tracker; the CI job `tests` fails if any of these regressions return.

**Error tracking rule:** every error records *how it happened* (reproduction/observation + root cause) and the *solution path* (fix, why it was chosen, regression test). No invented data.

**Linear:** team `PRO`, project **"BetterCI (VibeCI) - CI Hardening & Error Tracking"**. Issues `PRO-43` to `PRO-50` mirror E-01, E-03 to E-09 below, each with the sections "How it happened", "Root cause", "Solution path" and "Regression test". Example: https://linear.app/projects01/issue/PRO-43/e-01-high-patch-gate-accepted-patches-that-introduced-a-new-lint-rule

| ID | Severity | Component | How it happened | Root cause | Solution path | Regression test |
|---|---|---|---|---|---|---|
| E-01 | High | `vibeci/gate.py` | A patch removed one F401 but added one S110 and was still accepted | No-regression compared the **total** finding count | Compare a per-rule-code histogram; add ShellCheck no-regression for shell files | `test_gate_rejects_new_ruff_rule_even_if_total_count_drops`, `test_gate_rejects_new_shellcheck_finding` |
| E-02 | High | `vibeci/diffmap.py` | `scan` crashed on this repository: `fatal: ambiguous argument 'HEAD~1...HEAD'` | Repos with a single commit or shallow clones have no `HEAD~1` / `origin/<base>` | Resolve the base ref; fall back to a diff against Git's empty tree | `test_missing_base_ref_falls_back_to_empty_tree`, `test_scan_survives_root_commit_without_parent` |
| E-03 | High | `scripts/install-llama.sh` | Cached extracted `llama-server` was executed without any verification (cache poisoning) | Only the downloaded archive was hashed; a cache hit trusted the extracted directory | Re-hash the archive on every run and re-extract the binary from it | `test_install_llama_reverifies_cached_archive_every_run` |
| E-04 | High | `vibeci/tools.py` | Deterministic workflow autofixes never appeared | zizmor 1.30.1 marks every fix `unsafe`, so `--fix=safe` applies nothing | Run `--fix=all` on a scratch copy; original file untouched; each hunk still passes the gate | `test_zizmor_autofixes_actually_produce_suggestions` |
| E-05 | Medium | `vibeci/llm.py` | PR-controlled text could break out of the `<code>` prompt wrapper | Only `</code>` was escaped | Per-request random `VIBECI_UNTRUSTED_*` markers around all untrusted data | `test_prompt_wraps_untrusted_source_in_random_markers` |
| E-06 | Medium | `vibeci/diffmap.py` | Added line `+++ foo` inside a hunk was parsed as a `+++ b/...` file header | Header detection ignored hunk state | Only accept a `+++` header outside a hunk | `test_added_line_that_looks_like_a_file_header_is_not_a_header` |
| E-07 | Medium | Dev setup | `pip install --require-hashes --no-deps -r requirements.lock pytest` exited 1 | `pytest` had no hash | Separate hash-locked `requirements-dev.lock` | `test_lock_files_hash_every_pinned_package` |
| E-08 | Medium | `vibeci/cli.py` | `post` raised `FileNotFoundError` when no `review.json` existed | Missing output was not handled | Log and return 0 | covered by the integration suite |
| E-09 | Low | Repository | No CI workflow, no explicit Ruff config, no contributor/security docs, mixed-language documentation | Repository was an unvalidated alpha | `.github/workflows/ci.yml`, `pyproject.toml`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, English-only docs | `test_workflows_are_least_privilege_bounded_and_injection_safe`, `test_committed_fix_schema_matches_pydantic_model` |

## Tracking gap

The Linear issue for **E-02** could not be created: the Linear connector's provider-side security filter rejected the issue text before it reached Linear. The content was not resent and was not reworded to bypass the filter. E-02 is therefore tracked only here and on the Notion page; create the issue manually in the project if you want it in Linear too.

## Upstream / external observations (not repo bugs)

- zizmor 1.30.1 reports every available fix as `unsafe` — see E-04. Behaviour may change in a future release; the regression test will catch it.
- GitHub started migrating the `ubuntu-latest` label to Ubuntu 26 in October 2026. CI pins `ubuntu-24.04`.
- The 4.7 GB GGUF model hash could not be re-downloaded in the audit environment; `scripts/fetch-model.sh` verifies it at runtime.

## Vercel

Vercel was a misunderstanding in the original request: error tracking is done in **Linear** (see above), not Vercel. The Vercel connector is not connected in this workspace, and BetterCI is a GitHub Action rather than a Vercel-deployed web app, so no Vercel project, deployment or error data exists. Nothing was invented. See `SECURITY.md` for the private reporting path.
