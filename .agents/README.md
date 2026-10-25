# BetterCI agent protocol

This directory is the version-controlled policy entry point for humans, local models and optional external agents. Runtime state is **not** committed: `.agents/local/` and `.vibeci/` are ignored.

## Mandatory lifecycle

```text
GOAL -> PLAN -> HUMAN APPROVAL -> ISOLATED WORKTREE -> IMPLEMENT -> DETERMINISTIC VERIFY
     -> INDEPENDENT REVIEWS -> JUDGE -> PR (human merges)
```

1. **Goal:** state one objective, measurable acceptance criteria and evidence references (issue/PR URL, repository path, rule ID or commit). No reference means no autonomous work.
2. **Plan:** read-only. List exact paths, intended creations, checks, dependencies and restoration point. The planner cannot write files.
3. **Approval:** `/approve <plan-id>` or `/ok <plan-id>` is required before writes. Approval is scoped to one immutable plan digest.
4. **Isolation:** each worker uses its own Git branch and linked worktree under `.vibeci/worktrees/`. Workers never edit the controller worktree.
5. **Implement:** tools are allowlisted per role. File deletion, protected-path changes, unexpected paths and destructive Git operations are blocked.
6. **Verify:** deterministic checks run before an LLM opinion. Syntax, tests, linters, diff scope, line-retention, documentation language and suspicious-added-code checks must pass.
7. **Verifier role review:** advanced mode asks separate read-only role passes for correctness and security. Multiple calls to one model are reviewers, **not independent models**; reports must say so.
8. **Judge:** combines evidence, never votes away a deterministic failure. Any unresolved rejection means `needs_human`.
9. **Restore:** a failed or runaway worker is stopped, its worktree is reset to its recorded baseline, and a new attempt starts from that baseline. The controller worktree is never hard-reset.
10. **Publish:** produce a detailed English PR with goal, plan ID, task evidence, checks, risks, research links and unresolved items. The system never merges.

## Non-negotiable invariants

- All repository documentation and user-facing output are English.
- Existing files are not deleted by agents. Large line loss fails closed.
- A model cannot run arbitrary shell commands. Commands come from reviewed `.agents/settings.yml` argv lists.
- Untrusted content never becomes a shell command, Git ref, path outside the repository, or provider credential.
- Harmful/suspicious code produces a report-only overall finding; no generated one-click fix is offered.
- Model output is a hypothesis. Schemas constrain syntax; deterministic evidence establishes acceptance.
- Claims cite a path/rule/commit/URL. Missing evidence is reported as `unknown`, never invented.
- Cloud providers are opt-in and disabled unless a concrete, tested adapter and secret policy exist.
- Stop a reviewer after two invalid schema responses, a timeout, policy breach, repeated unsupported claims, or its configured budget. Stopping one reviewer cannot weaken the required approval count.

## Modes

- **standard:** scanners, patch gate, one bounded local proposer, human applies suggestions.
- **advanced:** explicit goal/plan/approval, isolated worktrees, deterministic verification and extra review evidence. It is slower and opt-in.

The shipped PR suggestion path performs two same-model verifier role passes and a fail-closed deterministic aggregation. The task controller ends with evidence for a human decision; automated worker launch, cross-model quorum and judge-controlled commit integration remain explicitly future work in [`FUTURE.md`](../FUTURE.md).

See [`commands.md`](commands.md) for the parser contract and [`settings.yml`](settings.yml) for exact limits.
