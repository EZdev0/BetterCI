# Future development

**Updated:** 2026-10-26

This file separates shipped behavior from planned work. A checkbox is completed only when code, tests and documentation exist; names alone are not implementations.

## Shipped in the current branch

- [x] Mandatory `AGENTS.md` -> `.agents/` policy reading order.
- [x] Machine-readable settings for goals, approvals, teams, allowed/denied/ignored paths and extensions, deletion limits, context, checks, research and provider status.
- [x] Safe slash parser: goal/plan/task commands plus `/ask`, `/answer`, `/teams`, `/brief`, `/note`, `/context`, `/notifications`, `/ack` and proposal-only `/schedule`.
- [x] Big Brain team map with one distinct manager per team, provider-neutral subagent briefs and explicit `provider_execution: not_started` disclosure.
- [x] Blocking human questions, deterministic high-risk request signals and local durable notifications; a response cannot override policy.
- [x] Conflict-free timestamped local team journals plus bounded context/manifest compaction with omitted-record SHA-256 evidence.
- [x] Evidence-backed goal and schema-constrained local-model plan; model plans are rejected for stale goal IDs, omitted goal criteria/references, hallucinated or out-of-scope paths, unsafe paths and bad dependencies.
- [x] Human approval bound to an immutable plan SHA-256 digest and clean baseline commit.
- [x] Dedicated Git branch + linked worktree for every task attempt.
- [x] Restore guard that operates only in a marker-validated managed worktree, resets tracked files to the baseline and preserves untracked files.
- [x] Planned/allowed path scope, hidden directory/extension and denied-glob enforcement, protected paths, no file deletion/rename, changed-file cap, actual removed-line threshold, added-file size cap and English-documentation check.
- [x] Suspicious added code becomes report-only (no generated fix).
- [x] Standard and advanced PR review modes. Advanced model-generated patches require two read-only verifier role passes plus fail-closed aggregation after the deterministic gate, with disclosure that one endpoint/model is not two independent models.
- [x] Reproducible `evaluate-model` smoke harness; it records real live-server results and never ships fabricated benchmark numbers.
- [x] Pinned 3B fast candidate in addition to the 7B default.
- [x] Review-post permission failures produce failing GitHub annotations and link the official least-privilege token guide.
- [x] Disabled-by-default tokenless SearXNG JSON search and allowlisted direct text fetch with strict URL/time/byte/result/no-redirect controls.
- [x] Least-privilege, read-only GitHub issue/PR diagnostics for help, exact target CI, runs, assigned runners and failed steps. The adapter executes an exact default-branch event SHA, combines dual repository-role checks with a secret-backed login/user-ID/control-issue allowlist, supports only default/same-repository-PR targets and never checks out contributor code.

## Next — required before stateful or autonomous execution from GitHub comments

- [ ] Persist signed/validated agent state for PR and issue comments without trusting user-edited comments.
- [ ] Add a stateful `/vibeci` command adapter only after it can preserve approval/worktree/recovery semantics across ephemeral GitHub-hosted jobs. The shipped comment adapter is diagnostics-only.
- [ ] Convert an approved plan into linked Linear/GitHub tasks idempotently.
- [ ] Add durable worker execution. GitHub-hosted runners are ephemeral; worktrees cannot survive between separate jobs without a deliberate artifact/ref protocol.
- [ ] Add judge-controlled candidate integration by cherry-picking verified task commits into a new integration branch. Never auto-merge the PR.
- [ ] Add tamper-evident JSONL audit events for prompts, model/role identity, tool decisions, checks, commits and verdicts.

## Provider adapters (opt-in, no fake compatibility)

- [ ] Define a provider contract with structured input/output, deadlines, cancellation, model identity and cost/token disclosure.
- [ ] Claude Agent SDK adapter: deny-first tools, `dontAsk`/plan semantics, pre/post hooks, no `bypassPermissions`.
- [ ] Google ADK/Gemini adapter: callbacks, explicit tool set, evaluation evidence and isolated task state.
- [ ] Jules adapter only after an official supported API, authentication flow and CI-safe execution contract are verified.
- [ ] Cross-model quorum: require genuinely different provider/model identities; correlated retries from one model do not count.
- [ ] Provider outage fallback to deterministic report-only mode, never to weaker permissions.

## CI scheduling

- [ ] Add an optional, reviewed scheduled verification workflow at a non-hour boundary.
- [ ] Document UTC/timezone behavior, five-minute minimum, delay/drop behavior, 60-day public-repository inactivity disablement, concurrency and billing.
- [ ] Allow only named internal operations (`/verify`, dependency audit, model smoke evaluation). Never schedule model-generated shell text.
- [ ] Add manual `workflow_dispatch` and a visible kill/disable procedure.

## Verification depth

- [ ] Run 3B vs 7B cold/warm evaluations on identical `ubuntu-24.04` runners; commit raw JSON and methodology.
- [ ] Expand smoke cases into a versioned corpus with vulnerable and already-fixed controls, deletion/truncation cases and prompt-injection cases.
- [ ] Add mutation tests proving that the gate catches intentionally wrong patches.
- [ ] Add language-specific gates beyond Python/Shell/YAML/C/C++.
- [ ] Add test-delta analysis: reject removed assertions or reduced coverage unless explicitly approved.
- [ ] Add provenance/SBOM and signed release verification.

## Product/UI

- [ ] PR summary showing goal, plan digest, task branches, checks, reviewer identities, evidence links and unresolved risks.
- [ ] Issue view with the same IDs as PR output and Linear, without duplicate tasks.
- [ ] `/explain <finding-id>` and `/evidence <finding-id>` read-only commands.
- [ ] Provider-neutral settings documentation and migration tooling.
- [ ] Authenticated opt-in external notification adapters with redaction, retry/idempotency and delivery integration tests; local/GitHub notices remain the only shipped sinks.
- [ ] Optional managed SearXNG deployment documentation and health checks; never hard-code or silently rotate through public instances.

## Exit criteria for “autonomous” wording

Do not describe BetterCI as autonomously implementing repository changes until all of these are demonstrated in tests and a real PR: durable state, isolated worker, stop/cancel, restore, deterministic checks, two genuinely independent reviewers or explicit same-model disclosure, judge integration, complete audit trail and human-controlled publication. “Autonomous” never includes merging.
