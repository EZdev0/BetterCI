# Security Policy

## Supported version

This project is a v0.1 alpha. Only the current default branch and the latest release are supported.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting ("Security" → "Report a vulnerability") for this repository. Include a reproduction, the affected commit, and the impact. You will receive an acknowledgement within 7 days.

## Threat model

VibeCI runs on untrusted pull-request content and has access to a `GITHUB_TOKEN`. The following boundaries are intentional:

- **PR content is data, never instructions.** The model receives one finding and a numbered source window inside a per-request random `VIBECI_UNTRUSTED_*` marker block. It has no tools and can only answer through the forced schema (`FixProposal`).
- **Model output is untrusted.** It passes Pydantic validation, a line-window check, line-number/fence checks, the diff-visibility clamp and `vibeci/gate.py` before anything can be posted. The gate rejects patches that introduce a new lint, ShellCheck or compiler error (compared per rule code, not by total count).
- **The LLM endpoint is loopback-only.** `LLMClient` rejects any non-`127.0.0.1`/`localhost` URL, so source code cannot leave the runner.
- **Supply chain.** All third-party actions are pinned to full commit SHAs. `requirements.lock` and `requirements-dev.lock` are installed with `--require-hashes --no-deps`. The llama.cpp archive is SHA-256-verified on every run and its binaries are re-extracted from the verified archive; the GGUF model is hash-verified by `scripts/fetch-model.sh`. A mismatch fails closed.
- **Least privilege.** Workflows declare `permissions: {}` at the top level and grant `contents: read` per job. Checkouts use `persist-credentials: false`. `pull_request_target` is not used; fork PRs get a read-only token and suggestions stay in annotations and the job summary.

## Out of scope

Denial-of-service through extremely large files in the scanned repository, malicious code executed by a scanner itself on attacker-controlled input, and compromises of upstream models/tools before their hashes were recorded. Report them anyway if you find a concrete bypass.
