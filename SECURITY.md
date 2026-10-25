# Security Policy

## Supported version

This project is a v0.1 alpha. Only the current default branch and the latest release are supported.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting ("Security" → "Report a vulnerability") for this repository. Include a reproduction, the affected commit, and the impact. You will receive an acknowledgement within 7 days.

## Threat model

VibeCI runs on untrusted pull-request content and has access to a `GITHUB_TOKEN`. The following boundaries are intentional:

- **PR content is data, never instructions.** The model receives one finding and a numbered source window inside a per-request random `VIBECI_UNTRUSTED_*` marker block. It has no tools and can only answer through the forced schema (`FixProposal`).
- **Model output is untrusted.** It passes Pydantic validation, a line-window check, line-number/fence checks, the diff-visibility clamp and `vibeci/gate.py` before anything can be posted. The gate rejects patches that introduce a new lint, ShellCheck or compiler error (compared per rule code, not by total count).
- **The LLM endpoint is loopback-only.** `LLMClient` rejects any non-`127.0.0.1`/`localhost` URL, so source code cannot leave through the model path. Optional web research is a separate disabled-by-default boundary: only its query or explicitly requested allowlisted URL is sent to the configured endpoint/host, and returned text remains untrusted.
- **Supply chain.** All third-party actions are pinned to full commit SHAs. `requirements.lock` and `requirements-dev.lock` are installed with `--require-hashes --no-deps`. The llama.cpp archive is SHA-256-verified on every run and its binaries are re-extracted from the verified archive; the GGUF model is hash-verified by `scripts/fetch-model.sh`. A mismatch fails closed.
- **Least privilege.** Workflows declare `permissions: {}` at the top level and grant `contents: read` per job. A consumer that posts same-repository suggestions adds only `pull-requests: write`, not all permissions. Missing/insufficient posting access fails with GitHub's official permission-help URL. Checkouts use `persist-credentials: false`; `pull_request_target` is not used, and fork PRs stay read-only.
- **Advanced writes need scoped approval.** A goal needs acceptance criteria and evidence references; the plan digest and clean baseline commit are approved before a task worktree is created. Model text cannot approve itself.
- **Questions stop workers.** Deterministic request triage and `/ask` create durable blocking questions for suspicious/unclear work. A human answer is recorded but cannot override checks; `/start` remains blocked until all questions are answered.
- **Team context is local and conflict-free.** Each expert team has one manager. Notes are separate mode-`0600` records in ignored `.vibeci/bigbrain/`; compression never rewrites them and fingerprints omitted records. Note/search/diff content is always untrusted.
- **Worker isolation and recovery.** Each attempt uses a dedicated branch/worktree. Protected/unplanned/hidden/denied paths, file deletion/rename, excessive actual removed lines (even in same-length rewrites), symlink escapes, non-English docs and suspicious added code fail closed. Restore is marker/branch/path checked, resets only tracked files in the worker worktree and never runs `git clean`.
- **Suspicious code is report-only.** Pattern matches are triage signals and never receive a generated fix. A human determines whether the code is malicious or legitimate. Common token/password/private-key shapes are redacted before verification output enters durable evidence.
- **No hidden provider escalation.** Claude, Gemini and Jules adapters are disabled in `.agents/settings.yml`; no missing local model can silently send code to a cloud service. Advanced role calls disclose when they share one endpoint/model.
- **PR comment commands are diagnostics-only.** The `issue_comment` adapter accepts a small case-insensitive read-only command grammar from `OWNER`, `MEMBER`, or `COLLABORATOR` actors. It checks that role in both workflow and Python, executes only default-branch code, never checks out the contributor ref, installs no dependencies, uses no cache or repository secrets, and grants only `actions: read`, `contents: read`, `pull-requests: read`, and `issues: write` for its response. It shows bounded run/job metadata and failing step names, never log contents. Stateful goal/plan/worker commands remain local until durable state and ephemeral-runner restoration have a tested design.

## Out of scope

Denial-of-service through extremely large files in the scanned repository, malicious code executed by a scanner itself on attacker-controlled input, and compromises of upstream models/tools before their hashes were recorded. Report them anyway if you find a concrete bypass.
