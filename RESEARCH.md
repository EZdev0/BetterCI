# Research log

**Last updated:** 2026-10-25 (Europe/Berlin)  
**Method:** official documentation and official GitHub repositories were searched/read through the web interface. No source repository below was cloned or executed during this update. Runtime claims remain unverified until a reproducible local/CI measurement is committed.

This is the canonical append-only research log for current architecture decisions. The older audit snapshot remains in [`docs/RESEARCH.md`](docs/RESEARCH.md).

## Evidence standard

- **VERIFIED:** confirmed in this repository by code/tests, or stated directly by a primary source.
- **SOURCE CLAIM:** stated by a primary source but not reproduced here.
- **UNVERIFIED:** hypothesis or candidate that needs a committed benchmark.
- Every new time-sensitive claim must record an access date and URL here. Search snippets are discovery aids; implementation decisions should prefer the linked primary document/repository.

## Access log — 2026-10-25

| Source (accessed 2026-10-25) | Observed primary-source fact | Decision in BetterCI |
|---|---|---|
| [GitHub Actions events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) | `schedule` uses five-field POSIX cron, runs on the latest default-branch commit, has a 5-minute minimum interval, may be delayed/dropped under load, and public-repository schedules disable after 60 inactive days. `issue_comment` covers both issues and PR comments and runs from the default branch. | `/schedule` validates proposals but never silently writes workflow YAML. Stateful comment execution is not shipped yet. |
| [GitHub secure use](https://docs.github.com/en/actions/reference/security/secure-use) | Least privilege; avoid unnecessary `pull_request_target`; never interpolate untrusted expressions directly into shell; privileged workflows must not execute untrusted PR code. | No PR/issue text becomes shell input. Current CI keeps `permissions: {}` and does not add a privileged comment runner. |
| [GitHub cache security](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching) | Low-trust triggers such as `issue_comment`, `workflow_run` and `pull_request_target` can poison privileged caches if write-capable cache mode is enabled; current defaults restrict those triggers. | Do not cache agent output from low-trust command events; archives/models are re-hashed before execution. |
| [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits) | Workflow/job, matrix, queue and event-rate limits exist and runs can be cancelled when limits are hit. | Agent budgets and timeouts are mandatory; GitHub Actions is not a persistent agent daemon. |
| [Git worktree](https://git-scm.com/docs/git-worktree) | A repository can have linked working trees with separate `HEAD`/index state while sharing repository objects and refs. | Each approved task gets a dedicated branch + worktree; restoration is scoped to the marked worktree. |
| [Google Antigravity overview](https://antigravity.google/docs/overview/) | **SOURCE CLAIM:** multi-agent orchestration, asynchronous subagents, isolated Git worktrees, scheduled tasks and verification loops are first-class concepts. | Used as an architecture reference only; Antigravity is not a BetterCI dependency or a GitHub-hosted runner guarantee. |
| [Google Antigravity subagents](https://antigravity.google/docs/subagents/) | **SOURCE CLAIM:** subagents can inherit/share or use branch workspaces; orchestrators use independent verification loops. | BetterCI chooses branch worktrees and forbids shared writable worker state. |
| [Google Antigravity schedule command](https://antigravity.google/docs/commands/schedule) | **SOURCE CLAIM:** schedules are managed tasks that can be inspected and killed. | BetterCI records schedule proposals and kill/stop state, but leaves actual recurring GitHub workflow creation to a reviewed PR. |
| [Google ADK repository](https://github.com/google/adk-python) and [technical overview](https://google.github.io/adk-docs/get-started/about) | ADK exposes deterministic sequential/parallel/loop workflow agents, callbacks and built-in evaluations; it is model-agnostic. | Deterministic controllers own lifecycle and policy; models are bounded proposers/reviewers. No ADK runtime dependency is needed for the current small core. |
| [Google ADK multi-agent pattern](https://google.github.io/adk-docs/agents/multi-agents/) | A refiner/checker loop needs a hard maximum iteration count and explicit escalation/stop condition. | Maximum attempts are 2; deterministic failure cannot be voted away. |
| [Google ADK safety](https://google.github.io/adk-docs/safety) | Callbacks, sandboxing, evaluation and tracing are recommended safety layers. | `.agents/settings.yml`, policy guards, worktrees, command evidence and tests supply equivalent local control points. |
| [Gemini CLI subagents](https://github.com/google-gemini/gemini-cli/blob/main/docs/core/subagents.md) | Subagents have separate context and explicit tool sets; subagents cannot recursively invoke subagents. | Roles are isolated and tool-scoped; recursive worker spawning is not allowed. |
| [Gemini CLI worktree change](https://github.com/google-gemini/gemini-cli/pull/22973) | The official implementation preserves worktrees that have untracked files or commits instead of deleting work blindly. | `/restore` resets tracked files only and deliberately preserves untracked files. |
| [Claude Agent SDK permissions](https://platform.claude.com/docs/en/agent-sdk/permissions) | `allowed_tools` auto-approves but does not restrict; `disallowed_tools`/deny rules are required to block. `bypassPermissions` is dangerous and inherited by subagents. | External adapters must use deny-first policy; BetterCI ships no bypass-permission adapter. |
| [Claude Agent SDK hooks](https://platform.claude.com/docs/en/agent-sdk/hooks) | Pre/post hooks can block dangerous operations, log calls and request human approval; deny wins over ask/allow. | Worker role policy requires pre-write scope checks; the shipped controller independently checks the resulting diff before verification. A future provider adapter must enforce both hook points. |
| [Claude Agent SDK subagents](https://platform.claude.com/docs/en/agent-sdk/subagents) | Read-only reviewers can be limited to Read/Grep/Glob and receive an isolated context. | Planner/verifier/judge roles are read-only by contract. |
| [Claude Agent SDK Python](https://github.com/anthropics/claude-agent-sdk-python) | The official SDK exposes hooks and tool permissions. | Listed as a future opt-in provider adapter; not installed or claimed as functional today. |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) | The official SDK exposes guardrails, human-in-the-loop, sessions and tracing. | Provider-neutral lessons recorded; no OpenAI adapter or external tracing is enabled. |
| [Qwen2.5-Coder official family post](https://qwenlm.github.io/blog/qwen2.5-coder-family/) | The official family has 0.5B/1.5B/3B/7B/14B/32B sizes; 3B is 3.09B parameters/32K context, while 7B is Apache-2.0 and larger. | 3B Q4_K_M is added as an opt-in fast candidate; 7B remains default until an apples-to-apples runner evaluation proves a better trade-off. |
| [Qwen2.5-Coder official repository](https://github.com/QwenLM/Qwen2.5-Coder) | Qwen publishes code-specific model families and evaluation material. | Prefer an instruct code model, but never use model benchmarks as proof that a patch is correct. |
| [Pinned 3B GGUF file](https://huggingface.co/bartowski/Qwen2.5-Coder-3B-Instruct-GGUF/blob/5b6d7b621cc8a18153323af1d96171ba0a8c4176/Qwen2.5-Coder-3B-Instruct-Q4_K_M.gguf) | File page reports revision `5b6d…4176`, 1.93 GB and SHA-256 `840490…1514`. | Added to `tools.lock.json`; runtime download still fails closed unless the full file hash matches. Not downloaded during this change. |

## Local model decision

There is no “perfect” local CI model. A grammar proves output shape, not truth, and repeated calls to one model are correlated. The current decision is:

1. **Default / quality profile:** keep `qwen2.5-coder-7b` Q4_K_M. It already has a reviewed lock entry and its 7B upstream license is Apache-2.0. It receives only one scanner finding and a small window.
2. **Fast candidate:** `qwen2.5-coder-3b-fast` Q4_K_M (1.93 GB) is available for opt-in smoke runs. It should download and infer faster, but that is **UNVERIFIED on GitHub’s runner** and its upstream licensing differs from the 7B model.
3. **Selection gate:** run `python -m vibeci evaluate-model` on the same runner image for both models, cold and warm. Record download time, total case latency, schema-valid rate, semantic pass rate and peak RSS. Do not change the default on file size or vendor benchmark alone.
4. **Correctness authority:** scanner evidence + syntax/compiler/linter/test checks + deletion/path/language/security policy + human review. The model never becomes the authority.

No real-model score is recorded in this commit because neither multi-gigabyte candidate was downloaded in this workspace. Mock-server integration tests exercise protocol behavior, not model quality.

## Standards adopted to catch false fixes

1. Immutable goal with measurable criteria and references.
2. Read-only plan with a digest; approval is bound to that digest.
3. One branch/worktree per task and a clean baseline SHA.
4. Deny-first path, deletion, language and suspicious-added-code guards.
5. Deterministic verification before model review.
6. Two verifier **role passes** in advanced mode; reports disclose when both use the same model.
7. A final judge must never overrule deterministic failure or missing evidence; automated task-commit judge integration remains future work.
8. Bounded attempts, time and changed-file scope; stop on invalid schema or unsupported claims.
9. Restore only tracked files inside a marker-validated managed worktree; preserve untracked user files.
10. PR remains human-merged.

## Related project patterns (not dependencies)

The primary sources above show converging patterns: Git worktree isolation, explicit role/tool scope, callbacks or guardrails, bounded refine/check loops, human approval and traceable evidence. BetterCI implements those patterns directly in a small Python control plane instead of importing a large agent SDK into the fast CI path.
