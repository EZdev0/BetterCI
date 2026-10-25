# Architecture

## Standard pull-request review

```text
PR diff ──> diffmap (visible RIGHT-side lines)
   │
   ├─> L1 zizmor (+ --fix=all on scratch copy) ──┐
   ├─> L2 Ruff / ShellCheck (+ Ruff safe edits)  ├─> Finding[]  (only lines in the diff)
   ├─> L3 Vulture / Radon                        │
   └─> L4 Trivy SARIF (separate pinned step)     ┘
                │
   tool_fix? ───┴─> gate ──> Suggestion (source=tool, no model)
   no fix + rule in LLM_RULES + budget left
                └─> llama-server (127.0.0.1) ─> FixProposal (schema)
                      ─> window/format checks ─> gate ─> [advanced: verifier role x2] ─> Suggestion
                                                                                         │
                       review.json ─> post (pull-requests: write, same-repo PRs) ─> suggestion blocks
```

**Standard mode** stops after the deterministic gate. **Advanced mode** adds two read-only verifier role calls for model-generated patches. Both calls use the configured endpoint/model unless a future provider adapter says otherwise; the report discloses this and never calls them independent models.

## Advanced task control plane

```text
AGENTS.md + .agents/settings.yml + .agents/bigbrain/teams.yml
       │
/goal (criteria + references) ─> deterministic request triage ─> [risk: blocking /ask + notification]
       │ safe
GoalSpec + goal digest ─> /plan ─> loopback planner ─> PlanProposal schema
       │                                      └─> visible path/reference/dependency validation
       │
/approve <plan digest> + clean baseline SHA
       │
/start <task> ─> dedicated branch + .vibeci/worktrees/<goal>-<task>-<attempt>
       │
worker edits only approved paths
       │
/verify <task> ─> change policy ─> reviewed argv checks ─> evidence ─> human
       │ failure                                                   │ success
/restore tracked files; preserve untracked                        later PR
```

`vibeci.agent` is a deterministic controller, not a free-form shell agent; fail-closed token/goal parsing and plan grounding are isolated in `vibeci.agent_parser`. `vibeci.safety` flags bounded instruction-override, destructive, credential-exposure, malware-construction and security-bypass language for human clarification; it does not infer a user's intent. The optional model may propose `PlanProposal`; Python validates every existing path against `git ls-files`, configured visibility/deny scope, new path safety, documentation scope, complete goal criteria/references, ordered dependencies and the current goal ID. Approval revalidates the plan and binds its SHA-256 digest to a clean baseline. Runtime state, model logs and worktrees are ignored by Git. Dependency status controls ordering only: every attempt still starts from the approved baseline because judge-controlled task-commit integration is not shipped.

The shipped controller intentionally does **not** execute Claude, Gemini, Jules or arbitrary provider CLIs. `.agents/settings.yml` marks these adapters disabled until a real tested adapter, cancellation path and secret policy exist. See [`FUTURE.md`](../FUTURE.md).

## Big Brain team and context plane

`.agents/bigbrain/teams.yml` is a strict committed map: each intake, planning, implementation, verification, security, research and documentation team has exactly one distinct manager, bounded experts/responsibilities and an explicit write policy. `/brief` builds a schema-shaped packet with an additional subagent system message, required first-read files, approved baseline comparison, open questions and required response fields. Its `provider_execution` value is `not_started`: packet creation is real orchestration data, not a claim that an external agent ran.

`/ask` or automatic high-risk triage creates a durable blocking question and warning notification. `/answer` records the human response but cannot approve, merge or override policy. Managed `/start` refuses to proceed while a question is open. Local notices are queryable/acknowledgeable; GitHub posting failures emit Action errors with the official permission-help URL. External notification delivery remains disabled because no authenticated adapter is shipped.

Team knowledge is not one conflict-prone mutable Markdown file. `vibeci.knowledge` writes one mode-`0600`, timestamped JSON record per note under ignored `.vibeci/bigbrain/teams/<team>/`. Shared brief context is rebuilt read-only, prioritizes warnings/decisions within configured limits, and represents every omitted record set with a count and SHA-256 digest. Planner manifests use the same bounded-context principle. Source records are never rewritten by compression.

## Change policy

Before reviewed test commands or a model verdict, `vibeci.policy` rejects:

- tracked file deletion or rename;
- a path outside the approved task, outside allowed globs, or inside configured ignored directories/extensions/denied globs;
- `.git`, workflows, lockfiles or agent-policy changes by a worker;
- too many files, oversized new files, excessive actual removed lines (including same-length rewrites) or an emptied file;
- symlink/path escapes;
- clearly non-English repository documentation;
- suspicious newly added patterns such as pipe-to-shell, destructive recursive deletion, dynamic `eval`/`exec`, world-writable permissions or token/network combinations.

A suspicious-code match switches the result to **report-only**: no generated one-click fix is emitted. It is a triage signal, not proof of malicious intent; a human security review decides.

`/verify` then runs only the literal argv arrays reviewed in `.agents/settings.yml`. No model text is evaluated as shell. A deterministic failure cannot be overruled by reviewer votes or a final judge.

## Restoration and stopping

Every task attempt has its own branch and linked Git worktree. `/restore` checks that:

1. the path is below the configured managed-worktree directory;
2. `.vibeci-worktree.json` matches the task, plan, branch and baseline;
3. the checked-out branch has the configured agent prefix; and
4. the worktree root is exactly the recorded path.

Only then does it run `git reset --hard <baseline>` **inside that worker worktree**. It never runs `git clean`; untracked user files remain. It never resets the controller worktree, rewrites a shared branch, pushes or merges. `/stop` records the ID and prevents a stopped task attempt from being verified; durable process cancellation is still future work because the current controller does not launch provider processes.

## Trust boundaries

- PR, issue, goal, source, comment, local-note, search-result and fetched-page content is untrusted input: model prompts wrap model-facing code/evidence in per-request random `VIBECI_UNTRUSTED_*` marker blocks so input cannot forge the end marker.
- Models have no tools in the standard scan path and output only through strict Pydantic/JSON schemas.
- Model output is untrusted: Pydantic validation, window check, line-prefix/fence check, evidence allowlist, patch gate, HTML/@-mention stripping and dynamic code-fence length.
- No-regression is per rule code: a patch removing F401 but adding S110 is rejected even when total count stays flat.
- Model plans are proposals: stale goal IDs, hallucinated/hidden tracked paths, unsafe new paths, invalid dependency order and unsupported evidence references fail closed.
- Supply chain: hash-locked Python dependencies, SHA-256 for the llama.cpp archive on every run, extracted binaries rebuilt from the verified archive, commit-pinned model URL + SHA-256, and full-SHA third-party action pins. Mismatch means failure.
- The LLM client refuses non-loopback URLs. Optional cloud providers are not silently enabled.
- Human approval is required before advanced writes; human merge remains mandatory.

## Optional web research boundary

`vibeci.research.WebResearchClient` is disabled until `research.enabled` and a reviewed SearXNG base URL are configured. Search calls only the documented JSON route; direct fetch requires an exact host allowlist. Both enforce HTTPS except explicitly allowed loopback tests, forbid URL credentials/fragments and credential-like query data, cap timeout/bytes/results, omit authorization headers and refuse redirects. Response text remains marked untrusted. BetterCI does not bundle SearXNG, select a public instance, bypass a site's access controls or promise a third-party free tier.

## Grammar vs. correctness

A GBNF/JSON-schema grammar guarantees syntax only. It cannot stop a model from proposing a wrong but well-formed patch. Correctness evidence comes from the deterministic gate, reviewed verification commands, bounded role review and human review. Two calls to one local model are correlated and never described as two independent AIs.

## Model profiles

- `qwen2.5-coder-7b` Q4_K_M remains the default quality profile.
- `qwen2.5-coder-3b-fast` Q4_K_M is an opt-in 1.93 GB fast candidate.
- `python -m vibeci evaluate-model` runs three live schema/gate/semantic smoke cases and records JSON. It is deliberately called a smoke evaluation, not a repository-scale benchmark.
- The default changes only after both candidates run cold and warm on identical runner images with committed raw results.

## Cache

`~/.vibeci` (venv, llama.cpp archive + extracted build, model) is one `actions/cache` entry keyed by both lockfiles and model. Default cache quota and retention can change; consult the dated source in `RESEARCH.md`. Model and llama.cpp archives are re-hashed after restore; the extracted build is recreated from the verified archive on every run.

## GitHub PRs, issues and schedules

Standard scans already expose findings through PR annotations, reviews, the job summary and the `vibeci-review` JSON artifact. Same-repository review posting requires only `contents: read` plus `pull-requests: write`; missing/insufficient access now returns a failing Action annotation with GitHub's official token-permission URL instead of silently succeeding. Fork PRs intentionally stay read-only. The slash parser is currently local/durable-state only. A privileged `issue_comment` executor is deliberately **not** shipped yet: GitHub comment events run default-branch code with write-capable issue/PR permissions, GitHub-hosted runners are ephemeral, and low-trust caches need special handling. `FUTURE.md` lists the requirements for a safe adapter.

`/schedule` therefore validates and records only a proposal for `/verify` or `/status`; it never writes workflow YAML. GitHub schedules use the latest default-branch commit, may be delayed or dropped, and are not a persistent exact-time daemon. Exact limits and the accessed official URL are in `RESEARCH.md`.

## Fork PRs

A future second workflow may be triggered by `workflow_run` to download a review artifact and post results, but it must never check out or execute untrusted PR code in the privileged job. Do not use `pull_request_target` as a shortcut.

## Known limits

- A real GGUF was not downloaded in this change. Protocol tests use a mock llama-server; model quality remains unmeasured here.
- The 3B file is pinned but is not the default until comparative runner evidence exists.
- Advanced verifier roles currently share one local endpoint/model; this is extra scrutiny, not model independence.
- The task controller creates/restores worktrees, questions, team briefs and verification evidence but does not launch a coding-provider process or integrate task commits. Provider execution and judge-controlled integration remain explicitly unshipped.
- Request triage is a bounded deterministic signal set, not a malware verdict; suspicious requests/code still require human security review.
- No SearXNG endpoint is configured by default, and public-instance availability is not assumed.
- `anthropic` transport depends on llama-server honoring forced tool choice; `doctor` probes it. Use `grammar` if it fails.
- Trivy runs outside the fast path; its SARIF parser exists, while a pinned install step remains future work.
