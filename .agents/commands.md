# Slash commands

Commands are ASCII, case-insensitive and parsed without shell evaluation. In GitHub comments use the `/vibeci` prefix; locally use `python -m vibeci agent`.

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
| `/schedule <five-field-cron> -- <safe-operation>` | Validate a future orchestration proposal for `/verify` or `/status`. It does not invoke the local task command or edit workflow YAML. | State only |

Unknown options, missing acceptance criteria, missing references, unsafe paths, arbitrary schedule commands and stale plan IDs fail closed. `/approve` never means merge. GitHub scheduled workflows run from the latest default-branch commit, can be delayed or dropped under load, use UTC by default, have a five-minute minimum interval, and public-repository schedules disable after 60 days without activity; see `RESEARCH.md`.
