# Repo-to-Go: working inside a repository with VibeCI

This guide explains the everyday workflow: how VibeCI looks at *your* repository,
how it analyses a pull request, how it proposes code changes, and exactly where a
human stays in control.

Every command on this page was run against this repository while writing it. If a
command is not shown here, it is not part of the shipped surface.

---

## 1. The mental model in one paragraph

VibeCI never asks a model "what is wrong with this code?". Deterministic scanners
(Ruff, ShellCheck, zizmor, Vulture, Radon) decide *what* is wrong. The model is
then handed one finding plus a small numbered source window and may only answer
through a strict JSON schema. Its patch is rebuilt, re-parsed and re-linted; if it
would introduce a new rule violation it is discarded. Only then is a GitHub
`suggestion` block posted. **A model never decides that something is a bug, and an
unverified model sentence is never published as a repository fact.**

That order is why the tool can run without API keys and why the output is reviewable.

---

## 2. What VibeCI does inside a repo (and what it does not)

| Capability | Shipped? | Who acts |
|---|---|---|
| Scan a diff and report findings on changed lines only | Yes | deterministic scanners |
| Post deterministic one-click `suggestion` blocks | Yes | tool output, no model |
| Propose a patch for a scanner finding | Yes | bounded local model, schema-forced |
| Accept a model patch | Only after the gate re-parses and re-lints it | deterministic gate |
| Record an evidence-backed goal and a task plan | Yes | human + planner role |
| Create an isolated branch + worktree for one approved task | Yes (`/start`) | deterministic code |
| **Autonomously write your feature code** | **No** | — |
| Launch a coding provider (Claude/Gemini/Jules CLI) | **No** — adapters are disabled | — |
| Approve a review, push to a protected branch or merge | **No** | human only |

`/start` is the step people most often misread. It creates the *workspace* for a
task attempt. It launches no model, no provider and no coding worker. The model's
in-repo role today is planning, patching individual scanner findings, and optional
verifier passes — not writing your feature.

---

## 3. Use case: analyse a pull request

### 3.1 Review scope is a diff, so choose the base deliberately

A finding is only reported when it sits on a line that the diff marks as changed.
The comparison base is therefore the most important flag you have.

```bash
# Compare the branch against the base branch (what CI does on a pull request).
python -m vibeci scan --no-llm --profile fast --base origin/main --head HEAD

# Review only the last commit.
python -m vibeci scan --no-llm --base HEAD~1 --head HEAD

# Review a specific commit range.
python -m vibeci scan --no-llm --base <base-sha> --head <head-sha>
```

Inside GitHub Actions you normally pass nothing: when `GITHUB_BASE_REF` is set the
scan uses `origin/<base>`, otherwise it uses `HEAD~1`.

### 3.2 The shallow-clone trap (read this before you trust a local number)

If the requested base ref does not exist — a `git clone --depth 1`, a root commit,
a deleted base branch — VibeCI does **not** crash. It falls back to a diff against
Git's empty tree, which puts *every line of every tracked file* in scope. It says so
out loud, and it records it in the artifact:

```text
[vibeci] base ref 'HEAD~1' not available; diffing HEAD against the empty tree (every line of every tracked file counts as changed)
[vibeci] 80 changed file(s): whole tree in scope because 'HEAD~1' does not exist (shallow clone or root commit); pass --base <ref> to narrow the review
```

`review.json` carries the same provenance, so a later reader cannot mistake a
whole-repository scan for a one-commit review:

```json
"diff_scope": {
  "requested_base": "HEAD~1",
  "effective_base": "4b825dc642cb6eb9a060e54bf8d69288fbee4904",
  "head": "HEAD",
  "full_tree_fallback": true
}
```

Two practical consequences:

1. Clone with full history when you want a narrow review: `git clone --no-single-branch` or pass an explicit `--base`.
2. Combining the fallback with `--fail-on error` turns every pre-existing error-level finding in the repository into a job failure. That is why the CI self-scan checks out with `fetch-depth: 0`.

The summary line states the scope that was really used, so a job log can never
claim a narrow comparison it did not perform.

### 3.3 Reading the output

```text
### VibeCI
- findings in changed lines: **12**
- one-click suggestions: **4** (3 deterministic, 1 local model)
- diff scope: `origin/main...HEAD`
- review mode: **standard**
- runtime: 6.1s
```

* `findings in changed lines` — findings that survive the diff filter; findings on
  untouched lines are intentionally dropped so the review is not noise.
* `one-click suggestions` — split into `deterministic` (a scanner produced the fix)
  and `local model` (the model proposed it and the gate accepted it).
* `diff scope` — the base/head actually compared, or `whole tree` when the fallback ran.

Artifacts land in `vibeci-out/` by default: `review.json` (suggestions + scope +
reviewer disclosure), `findings.json` (raw findings) and the GitHub step summary.

### 3.4 Profiles

| profile | scanners |
|---|---|
| `fast` | Ruff, ShellCheck, zizmor |
| `balanced` (default) | `fast` + Vulture, Radon |
| `deep` | currently identical to `balanced`; reserved for heavier scanners |

---

## 4. Use case: get gated fixes for what scanners found

Deterministic fixes need no model at all. Ruff safe edits and zizmor auto-fixes are
produced on a scratch copy, then every hunk still has to pass the gate.

For the rest, start a local model and point the scan at it:

```bash
scripts/install-llama.sh        # hash-verified archive, extracted binary rebuilt from it
scripts/fetch-model.sh          # commit-pinned GGUF + SHA-256
scripts/start-server.sh
python -m vibeci doctor --llm-url http://127.0.0.1:8080   # probes forced tool choice

python -m vibeci scan --llm-url http://127.0.0.1:8080 \
  --transport grammar --base origin/main --head HEAD --out vibeci-out
```

`--transport grammar` uses `/v1/chat/completions` with a `json_schema`
`response_format`; `anthropic` uses llama-server's Anthropic-compatible
`/v1/messages` route with forced `tool_choice`. If `doctor` reports that the server
does not honour forced tool choice, use `grammar`. The client refuses any non-loopback
URL, so no code can leave the runner by accident.

The model is capped by `--max-llm` (default 5 findings) and `--llm-budget`
(default 150 seconds). When the budget runs out the remaining findings are reported
without a generated fix rather than guessed.

### Advanced review mode

```bash
python -m vibeci scan --review-mode advanced --llm-url http://127.0.0.1:8080 ...
```

`advanced` adds two read-only verifier role passes per model-generated patch, after
the deterministic gate. Both passes use the configured endpoint and model, and the
report says so explicitly — two calls to one model are extra scrutiny, not two
independent reviewers. A deterministic failure cannot be overruled by verifier votes.

---

## 5. Use case: change the repository through the agent lifecycle

This is the "in-repo coding" path. It is a state machine with human gates, not a
free-form shell agent.

```bash
# 0. Show the parser contract.
python -m vibeci agent --text "/help"

# 1. Record a goal. It needs measurable criteria and real references.
python -m vibeci agent --text "/goal Harden parser behavior --accept 'All parser tests pass' --ref vibeci/agent.py"
# -> Goal fd121d71c505 recorded. Run /plan.

# 2. Ask the planner. This is the only step that needs a live loopback model.
python -m vibeci agent --llm-url http://127.0.0.1:8080 --transport grammar --text "/plan"

# 3. Inspect, then approve exactly one immutable plan digest.
python -m vibeci agent --text "/status"
python -m vibeci agent --text "/approve <plan-id>"

# 4. Create an isolated workspace for one task attempt.
python -m vibeci agent --text "/start <task-id>"

# 5. Edit inside that worktree, then run the reviewed checks.
python -m vibeci agent --text "/verify <task-id>"

# 6. On failure, reset only that managed worktree to its baseline.
python -m vibeci agent --text "/restore <task-id>"
```

What each gate actually enforces:

* **`/goal`** rejects a goal without measurable `--accept` criteria or without
  `--ref` evidence (path, rule, issue or URL). Vague goals fail closed.
* **`/plan`** returns a schema-valid proposal. Every existing path is validated
  against `git ls-files`, configured visibility and deny globs, new-path safety,
  documentation scope, dependency ordering and the current goal ID. A stale goal ID
  or an invented path fails the whole plan.
* **`/approve`** revalidates the plan and binds its SHA-256 digest to a clean
  baseline. `/approve` never means merge.
* **`/start`** creates a dedicated branch plus a worktree under ignored
  `.vibeci/worktrees/`. Nothing is launched.
* **`/verify`** first applies the deny-first change policy (no deletion or rename,
  no path outside the approved task, no `.git`/workflow/lockfile/agent-policy edits,
  bounded file counts and removal budgets, no symlink escapes, English documentation,
  no suspicious added patterns), then runs only the literal argv arrays reviewed in
  `.agents/settings.yml`. No model text is ever evaluated as shell.
* **`/restore`** refuses unless the path is below the managed worktree directory,
  its `.vibeci-worktree.json` matches the recorded task/plan/branch/baseline, the
  branch carries the agent prefix, and the worktree root matches exactly. It never
  runs `git clean`, so your untracked files survive.
* **`/stop <agent-id>`** blocks `start`, `verify` and `restore` for that ID. Only a
  human `/resume <agent-id>` reverses it, and it marks the task `failed` so a restore
  is still required before a retry.

Runtime state lives in ignored `.vibeci/` (state file mode `0600`), so running these
commands never dirties your working tree. Policies under `.agents/` stay version
controlled; mutable state never enters a commit.

### Asking the repo instead of guessing

```bash
python -m vibeci agent --text "/teams"
python -m vibeci agent --text "/ask security -- 'Is this credential flow intentional?' --ref app.py"
python -m vibeci agent --text "/brief security -- 'Review the approved diff'"
python -m vibeci agent --text "/note research evidence -- 'JSON output requires instance configuration' --ref RESEARCH.md"
python -m vibeci agent --text "/context"
```

`/ask` creates a durable blocking question, and `/start` refuses to proceed while one
is open. `/answer` records the human reply but cannot approve, merge or override
policy. Team notes are one immutable timestamped JSON record each under ignored
`.vibeci/bigbrain/`, so teams cannot overwrite a shared document; `/context` shows a
bounded view plus the omitted-record count and a SHA-256 digest.

### Letting the deterministic layer drive

```bash
python -m vibeci agent --text "/graph"     # Mermaid lifecycle + plan DAG from real state
python -m vibeci agent --text "/next"      # which tasks have verified dependencies
python -m vibeci agent --text "/bot goal_set"
```

`/graph` and `/next` never mutate state and never call a model. `/bot <event>` maps a
known event to one allowlisted command and returns `blocked` for anything unknown or
ambiguous; it reports `uses_model: true` only where a model really is involved. It
never executes the command it suggests.

---

## 6. Use case: analyse CI from a GitHub comment

After `.github/workflows/vibeci-commands.yml` is merged onto the default branch and
an administrator has set the `VIBECI_COMMAND_POLICY_JSON` secret, an authorised
actor can inspect CI without leaving the conversation:

| Comment | What you get |
|---|---|
| `/help` | both command surfaces, availability, authorization, limits |
| `/ci` | status, assigned runners and failed steps for the resolved target |
| `/runs` | up to five recent non-command runs for the exact target SHA |
| `/run <run-id>` | detailed jobs for one existing run, only if its SHA matches the target |
| `/runners` | runners assigned to target-bound jobs |
| `/failures` | failed/cancelled/timed-out jobs and failing step names with links |

Add `--target default` or `--target pr:<number>` to choose the scope. Without it, a PR
conversation resolves to that PR's head and a policy-listed control issue resolves to
the default-branch event SHA. Arbitrary refs, branch names and comment-supplied SHAs
are rejected.

Responses start with a single `@mention` of the verified actor who ran the command.
Comment text can never create another mention: untrusted `@` characters are escaped.
The surface is read-only — it cannot start, rerun, cancel, approve or merge anything,
and it never reads job logs or checks out contributor code.

The lifecycle commands from section 5 are **local only**. GitHub-hosted runners are
ephemeral, so a comment cannot carry goal/plan/approval/worktree state. A comment
using one of those names gets an explicit availability explanation instead of being
silently ignored.

---

## 7. What is deliberately not possible

* No autonomous implementation of your feature. `/start` prepares a workspace; the
  provider adapters in `.agents/settings.yml` are disabled until a real tested
  adapter, cancellation path and secret policy exist.
* No merge, no approval, no protected-branch push. Human merge is mandatory by design.
* No cloud fallback. The LLM client refuses non-loopback URLs, so planning and
  patching require a model you run yourself.
* No stateful slash commands from GitHub comments, and no durable cross-job worker
  state. See [`FUTURE.md`](../FUTURE.md) for the required design work.
* No promise that a model never reasons incorrectly. The guarantee is narrower and
  real: an unverified model claim is never published as a repository fact.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Huge finding count locally, small in CI | shallow clone triggered the empty-tree fallback | clone full history or pass `--base <ref>`; check `diff_scope.full_tree_fallback` |
| `base ref ... not available` | requested base does not exist | pass a ref that exists in this clone |
| `fatal: ambiguous argument 'HEAD~1...'` in an older version | single-commit or shallow repository | fixed by the empty-tree fallback (E-02); update |
| `/plan requires a configured loopback local model` | no `--llm-url`, and no provider fallback exists | start `scripts/start-server.sh` and pass `--llm-url http://127.0.0.1:8080` |
| `/tasks` says `no plan exists` | planning has not succeeded yet | run `/plan` with a live model, then `/approve` |
| `/verify` says `current plan is not approved` | missing approval gate | `/approve <plan-id>` first |
| `/start` refuses while a question is open | blocking `/ask` is unanswered | `/answer <question-id> -- "..."`, then retry |
| Suggestions missing on a fork PR | `pull_request` from a fork gets a read-only token | expected; results stay in annotations and the job summary |
| `anthropic` transport returns free text | server ignores forced tool choice | run `python -m vibeci doctor`, or switch to `--transport grammar` |
| Posting fails with 403/404 | token lacks `pull-requests: write` | grant `contents: read` + `pull-requests: write`; the step fails loudly with GitHub's permission guide |

---

## 9. Where to read next

* [`ARCHITECTURE.md`](ARCHITECTURE.md) — pipelines, trust boundaries, graphs, grounding.
* [`.agents/commands.md`](../.agents/commands.md) — the full local command table.
* [`AGENTS.md`](../AGENTS.md) and [`.agents/settings.yml`](../.agents/settings.yml) — policy you are agreeing to.
* [`ERRORS.md`](ERRORS.md) — every tracked defect, its cause and its regression test.
* [`../README.md`](../README.md) — installation as a GitHub Action.
