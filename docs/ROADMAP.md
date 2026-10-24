# 90-day roadmap (realistic version)

100k stars is an outlier outcome, not a plan. Plan for trust first: a small number of repos that keep VibeCI enabled.

**Days 1-14 - make it real.** Measure cold + cached with the commands in `README.md` (there is no benchmark workflow yet), fill the README table with measured numbers. Run `vibeci doctor` on a real llama-server. Fix what breaks. Tag v0.1.0 with a commit-pinned example.
**Days 15-30 - dogfood.** Enable on 5-10 own repos, measure fix acceptance rate (accepted suggestions / posted). Cut rules that nobody accepts.
**Days 31-45 - proof.** Reproducible benchmark repo: injected bugs, VibeCI vs. Ruff-only vs. a hosted reviewer; publish method + raw data.
**Days 46-60 - reach.** Show HN with the measured numbers and the "model is boxed in" design; r/programming and r/github; a short post on the Trivy-style supply-chain lesson and how the lock files prevent it.
**Days 61-75 - breadth.** C/C++ (cppcheck, clang-tidy parsers), fork-PR `workflow_run` workflow, SARIF upload, second model profile.
**Days 76-90 - retention.** Marketplace listing, release automation with signed tags, GitHub topics (github-actions, code-review, llama-cpp, devsecops), contributor guide with "add a scanner in 30 lines".

Success metrics: repos with the action enabled, suggestion acceptance rate, median runtime, issues from strangers. Stars follow those.
