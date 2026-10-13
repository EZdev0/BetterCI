# Research prompt for a second AI

You are a research analyst with web access. Today is 2026-10-13. Verify and extend the findings below for a project called VibeCI: a GitHub Action that runs a local ~7B GGUF model (llama.cpp, CPU only) on GitHub-hosted `ubuntu-latest` runners, after deterministic scanners, and posts native `suggestion` blocks on pull requests. Prefer primary sources (docs, release notes, source code, issue trackers). Never invent numbers or links. For every claim give: source URL, date, and confidence (high/medium/low). If you cannot verify something, say so.

## Hard questions (answer with measurements or cited evidence)
1. Real throughput of 3B, 4B, 7B and 9B Q4 models on 4-vCPU GitHub-hosted runners: prefill and generation tok/s, load time, and how it changes with `-t`, `--cache-reuse`, and `--spec-type ngram-mod`/`ngram-simple`. Look for published `llama-bench` runs on Actions runners; if none exist, propose a minimal reproducible benchmark.
2. Which model is best for "given one linter finding and a 20-line window, return a minimal correct patch" under a 16 GB RAM / 4 vCPU limit? Compare Qwen2.5-Coder-7B, Qwen3.5 (4B/9B, non-thinking mode), Gemma 4 small variants, Granite 4.x, Phi-4-mini, and any 2026 code-specific small model. Use patch-level benchmarks (e.g. SWE-style, CanItEdit, EditEval) rather than HumanEval alone. Note licenses.
3. Does llama-server's Anthropic-compatible `/v1/messages` route apply a hard grammar when `tool_choice` forces a tool? Read the llama.cpp source (server + chat parser) and issues. Is there any native structured-output equivalent (`output_config`) on that route?
4. Latest GitHub Actions changes that affect this design: workflow dependency locking, egress firewall, immutable actions, cache quota/eviction, `pull_request` vs `workflow_run` token rules for forks, runner image changes (ubuntu-26.04), Node runtime deprecations.
5. State of the Trivy/trivy-action compromise (2026): affected versions, safe versions, official guidance, and a safe way to run Trivy in CI today. Evaluate OSV-Scanner and Grype as alternatives.
6. Prompt-injection and cache-poisoning incidents involving AI agents in CI during 2025-2026 (e.g. "Clinejection"): root causes and concrete mitigations beyond "pin actions".
7. Existing open-source projects that already do scanner-first + local-model fixes in CI (PR-Agent, Gito, Kodus, reviewdog suggester, ai-action/ollama-action, others). For each: license, last release, local-model support, suggestion-block support, gaps VibeCI could fill.
8. Which linters/scanners give safe, machine-applicable fixes for C/C++ and Shell (clang-tidy fixits, cppcheck, ShellCheck fix objects, shfmt)? How to convert them to GitHub suggestions reliably.
9. Distribution: what actually drove adoption for comparable GitHub Actions/dev tools (case studies with dates and numbers). Be skeptical of "100k stars" claims.

## Output format
A Markdown report: (a) a table of claims with source, date, confidence; (b) a "contradicts our assumptions" section; (c) a ranked list of recommended changes to VibeCI with effort estimates; (d) a short list of things you could not verify.
