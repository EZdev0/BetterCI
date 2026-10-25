# Slash commands

Local control-plane commands are ASCII, case-insensitive and parsed without shell evaluation through `python -m vibeci agent`. Policy-authorized GitHub issue/PR comments expose a separate read-only CI diagnostic surface documented below; they do not invoke the stateful local controller.

| Command | Effect | Writes code? |
|---|---|---:|
| `/help` | Show valid commands and current safety rules. | No |
| `/goal <objective> --accept <criterion> [--accept ...] --ref <reference> [--ref ...]` | Validate and record a goal. | State only |
| `/plan` | Ask the configured local planner for a schema-valid, path-checked plan. | State only |
| `/status` | Show goal, immutable plan ID, approval and task/check state. | No |
| `/approve <plan-id>` or `/ok <plan-id>` | Human-approve exactly one plan digest. | State only |
| `/tasks` | List plan tasks, dependencies, worktrees and evidence. | No |
| `/start <task-id>` | Create a fresh branch and managed worktree for one approved task attempt. | Git metadata/worktree |
| `/verify <task-id>` | Run change guards and reviewed deterministic argv checks for one active attempt. | Reports only |
| `/review standard|advanced` | Select review depth; advanced requires an approved plan. | State only |
| `/restore <task-id>` | Reset a **managed worker worktree only** to its recorded baseline. | Worker worktree |
| `/stop <agent-id>` | Mark a worker/reviewer stopped and reject further output from it. | State only |
| `/ask <team> -- "<question>" [--ref <context>]` | Create a blocking human question and warning notification. | State only |
| `/answer <question-id> -- "<answer>"` | Record an answer. It does not override policy, approve a plan or resume a provider automatically. | State only |
| `/teams` | Show reviewed team managers, experts, responsibilities and write policy. | No |
| `/brief <team> -- "<request>"` | Build a bounded subagent packet with manager, baseline, open questions and compacted untrusted context. It does not launch a provider. | No |
| `/note <team> <kind> -- "<text>" --ref <evidence>` | Append one immutable timestamped note to ignored team-local knowledge. | Local state only |
| `/context` | Show bounded shared context plus omitted-record count and SHA-256 digest. | No |
| `/notifications [all|open]` / `/ack <notification-id>` | Inspect or acknowledge durable local notices, warnings and errors. | State only |
| `/schedule <five-field-cron> -- <safe-operation>` | Validate a future orchestration proposal for `/verify` or `/status`. It does not invoke the local task command or edit workflow YAML. | State only |

## GitHub issue and PR comment diagnostics

After `.github/workflows/vibeci-commands.yml` exists on the default branch and `VIBECI_COMMAND_POLICY_JSON` is configured, an exact policy-listed login/user-ID pair with repository role `OWNER`, `MEMBER`, or `COLLABORATOR` may use these commands. Normal issues must also appear in the policy's `control_issues` list. Commands and the optional `/vibeci` prefix are case-insensitive.

| Command | Effect |
|---|---|
| `/help` or `/vibeci help` | Show the GitHub diagnostic command contract, authorization and security boundaries. |
| `/ci [--target default\|pr:<number>]` | Show target-bound workflow runs, jobs, runner assignments and failed steps. |
| `/runs [--target default\|pr:<number>]` | List up to five non-command workflow runs bound to the exact target SHA. |
| `/run <run-id> [--target default\|pr:<number>]` | Show one run only if it belongs to the selected target's exact SHA. |
| `/runners [--target default\|pr:<number>]` | Show runners assigned to target-bound jobs, not an admin runner inventory. |
| `/failures [--target default\|pr:<number>]` | Show failed/cancelled/timed-out jobs and failing steps for target-bound runs. |

Without `--target`, PR conversations resolve to that PR's current head while policy-listed control issues resolve to the exact default-branch event SHA. The only explicit selectors are `default` and a positive same-repository `pr:<number>`; arbitrary refs, branches and SHAs fail closed. The workflow scans at most 50 candidate runs, excludes its own command runs, reports at most five runs, inspects 50 latest-attempt jobs per run, caps each REST response at 2 MiB with a 20-second timeout, and caps a posted response at 24,000 characters.

The workflow checks out only the event's exact default-branch SHA and never the contributor checkout. Only the Python handler receives the bounded policy secret. It validates repository, role, event actor/login/user-ID and control-issue scope before reading metadata; missing or malformed policy fails without printing its value. Comment/policy text never enters a shell, dependency, cache or checkout ref. The token requests only `actions: read`, `contents: read`, `pull-requests: read`, and `issues: write`; it cannot start, rerun, cancel, approve or merge. Because GitHub only activates an `issue_comment` workflow present on the default branch, new behavior becomes available after the reviewed workflow is merged.

Unknown options, missing acceptance criteria, missing references, hidden/unsafe paths, risky instruction overrides, arbitrary schedule commands, open blocking questions and stale plan IDs fail closed. `/approve` never means merge. GitHub scheduled workflows run from the latest default-branch commit, can be delayed or dropped under load, use UTC by default, have a five-minute minimum interval, and public-repository schedules disable after 60 days without activity; see `RESEARCH.md`.
