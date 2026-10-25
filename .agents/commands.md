# Slash commands

Local control-plane commands are ASCII, case-insensitive and parsed without shell evaluation through `python -m vibeci agent`. GitHub PR comments expose a separate read-only CI diagnostic surface documented below; they do not invoke the stateful local controller.

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

## GitHub PR comment diagnostics

After `.github/workflows/vibeci-commands.yml` exists on the default branch, an `OWNER`, `MEMBER`, or `COLLABORATOR` may use these commands in a pull-request conversation. Commands and the optional `/vibeci` prefix are case-insensitive.

| Command | Effect |
|---|---|
| `/help` or `/vibeci help` | Show the GitHub diagnostic command contract and security boundaries. |
| `/ci` or `/vibeci ci` | Show current-head workflow runs, job conclusions, assigned runner names/groups/labels and failed step names. |
| `/runs` or `/vibeci runs` | List up to five recent workflow runs bound to the pull request's current head SHA. |
| `/run <run-id>` or `/vibeci run <run-id>` | Show one run only if it belongs to the pull request's current head SHA. |
| `/runners` or `/vibeci runners` | Show runners actually assigned to current-head jobs. This is not an admin-level self-hosted runner inventory. |
| `/failures` or `/vibeci failures` | Show failed/cancelled/timed-out jobs and failing steps for current-head runs. |

Run/job views are bounded to five runs and 50 latest-attempt jobs per run; larger matrices remain available through the linked GitHub run. The workflow executes only committed default-branch code, never the contributor checkout. It reads the event payload as data, does not interpolate comment text into a shell, installs no dependencies, uses no cache, and never exposes job-log content. It requests `actions: read`, `contents: read`, `pull-requests: read`, and `issues: write`; it cannot rerun or cancel jobs. Because GitHub only activates an `issue_comment` workflow present on the default branch, this surface becomes available after the reviewed workflow is merged.

Unknown options, missing acceptance criteria, missing references, hidden/unsafe paths, risky instruction overrides, arbitrary schedule commands, open blocking questions and stale plan IDs fail closed. `/approve` never means merge. GitHub scheduled workflows run from the latest default-branch commit, can be delayed or dropped under load, use UTC by default, have a five-minute minimum interval, and public-repository schedules disable after 60 days without activity; see `RESEARCH.md`.
