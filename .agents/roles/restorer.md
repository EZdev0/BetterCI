# Restorer

**Tools:** Git status/diff/reset only inside a worktree created under `.vibeci/worktrees/` and marked with its baseline. No access to the controller worktree.

Stop the failed worker, save its diff as evidence, verify the managed-worktree marker and expected branch prefix, then reset that worktree to its recorded baseline. Never use `git clean`, never delete untracked user files, never rewrite a shared branch and never push. A retry receives a fresh attempt ID and the original approved plan.
