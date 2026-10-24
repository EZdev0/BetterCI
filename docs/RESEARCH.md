# Research Dossier (snapshot: 2026-10-13)

Confidence labels: **[VERIFIED]** checked directly (binary, tests, lockfiles) · **[LIKELY]** multiple sources, not measured by us · **[UNCERTAIN]** single source or inference.

This is a dated snapshot. Claims about third-party incidents and runner sizes are not re-verified on every commit; the CI workflow and the lockfiles are the executable part of this repository.

## 1. Runner

- **[LIKELY]** Public repos: 4 vCPU / 16 GB RAM / ~14 GB SSD on `ubuntu-latest`; private repos are smaller (2 vCPU). Source: GitHub Docs, "GitHub-hosted runners".
- **[LIKELY]** Cache: 10 GB default per repo, larger quota available for paid plans since 2025-11; entries unused for 7 days are evicted (GitHub Changelog, 2025-11-20).
- **[UNCERTAIN]** Real CPU token rates for a 7B Q4 model on these runners: no reliable measurement found. Inference from published blog numbers: prefill is likely in the tens of tokens per second. That is why `README.md` documents reproducible benchmark commands (a `benchmark.yml` workflow does not exist yet).

## 2. Local models in 2026

- **[LIKELY]** Small candidates: Qwen2.5-Coder-7B (Apache-2.0), Qwen3.5 4B/9B, Gemma 4 E4B, Granite 4.x. Large MoE coders (30B-A3B+) do not fit in 16 GB.
- **[VERIFIED]** Chosen model: Qwen2.5-Coder-7B-Instruct Q4_K_M (bartowski repo, commit + SHA-256 recorded in `tools.lock.json`, values taken from the Hugging Face file page). The download could not be tested in this environment; the hash check at runtime is the real test. A non-thinking model avoids reasoning overhead.

## 3. Claude SDK compatibility (new approach)

- **[VERIFIED]** llama-server b11429 contains `/v1/messages` and `/v1/messages/count_tokens` (strings in the binary; it converts Anthropic requests to OpenAI chat internally). Ollama also has Anthropic compatibility **[LIKELY]**.
- **[VERIFIED]** The pinned `anthropic` SDK 1.9.0 no longer has a `temperature` argument; the value is passed through `extra_body`. Found by the end-to-end test.
- **[LIKELY]** Running full Claude Code / Agent SDK against a 7B CPU model is a poor fit: large system prompt plus tool schemas, unstable tool calls from small models, prompt-cache invalidation (sources: llama.cpp discussion #14758, blog post "Claude Code, llama.cpp and the Hidden Prompt Cache Killer"). Hence: a thin `anthropic` SDK call with one forced tool. The Agent SDK is only optional for self-hosted/more powerful setups.
- **[UNCERTAIN]** Whether the Anthropic route applies a hard grammar when `tool_choice` forces a tool: `vibeci doctor` probes it live; fallback is `transport: grammar`.

## 4. Structured output

- **[LIKELY]** llama.cpp converts `json_schema` into GBNF; a grammar guarantees syntax only, not correctness. The claim "physically no hallucinations" is false; semantics require a gate (implemented here).
- **[VERIFIED]** The Python converter `json_schema_to_grammar.py` no longer exists in the current llama.cpp repo (C++ only). `grammars/fix.gbnf` is handwritten and has not been validated against the engine.

## 5. Security (critical)

- **[LIKELY]** March 2026: `trivy-action`/Trivy compromised (TeamPCP), roughly 75 tags redirected, CI secrets stolen. Consequence: SHA pins, checksums, no `@v1`.
- **[LIKELY]** February 2026 "Clinejection": prompt injection via issue title → cache poisoning → token theft in a CI AI agent. Consequence: model without tools, PR text treated as data, model/binary hash verified after cache restore.
- **[LIKELY]** GitHub's 2026 Actions security roadmap: dependency locking, egress firewall, scoped secrets (some announced; verify the status of each feature).
- **[VERIFIED]** Fork PRs receive a read-only token on `pull_request`; `pull_request_target` plus checkout of PR code is the classic attack.

## 6. Existing projects (learn from them, do not copy blindly)

PR-Agent (The-PR-Agent/pr-agent), Gito, Kodus (kodustech/kodus-ai-cr), hampsterx/pr-reviewer-action, saifullahsaeed/pr-review-action, ai-action/ollama-action, reviewdog (suggestions), zizmor, kodustech/awesome-ai-code-review. **[LIKELY]** All of them need a model endpoint or a cloud key; none combines scanner-first + local model on the runner + patch gate in one action.

## 7. New methods implemented or under review

1. Scanner fixes without a model (Ruff safe edits; zizmor autofixes applied to a scratch copy with `--fix=all`, because zizmor 1.30.1 marks every fix as `unsafe` and `--fix=safe` applies nothing).
2. Patch gate with no-regression checks instead of syntax checks only.
3. Diff-visibility clamp (GitHub only accepts suggestions on lines that are part of the diff).
4. Hard model budgets (maximum findings, wall-clock deadline).
5. **[UNCERTAIN]** `--spec-type ngram-mod` (model-free speculative decoding, fits "rewrite code"): present in b11429, benefit unproven; opt-in plus benchmark.
6. **[UNCERTAIN]** `--cache-reuse` with a static system prompt first in the prompt for KV prefix reuse.

## 8. Corrections to the original brief

- Radon/Vulture/Ruff are Python-only; C/C++ and shell need different scanners.
- 90 s cold / 30 s cached with 7B plus LLM is unlikely **[UNCERTAIN]**; only the deterministic stage is realistic. Measure it.
- "One YAML file with an embedded agent" is hard to maintain and cannot be pinned; use a composite action plus one consumer workflow instead.
- 100k stars is not a plannable goal.
