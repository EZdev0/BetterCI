# Architecture

```
PR diff ──> diffmap (visible RIGHT-side lines)
   │
   ├─> L1 zizmor (+ --fix=safe on scratch copy) ─┐
   ├─> L2 Ruff / ShellCheck (+ Ruff safe edits)  ├─> Finding[]  (only lines in the diff)
   ├─> L3 Vulture / Radon                        │
   └─> L4 Trivy SARIF (separate pinned step)     ┘
                │
   tool_fix? ───┴─> gate ──> Suggestion (source=tool, no model)
   no fix + rule in LLM_RULES + budget left
                └─> llama-server (127.0.0.1) ─> FixProposal (schema) ─> window/format checks ─> gate ──> Suggestion (source=llm)
                                                                                    │
                       review.json ─> post (pull-requests: write, same-repo PRs) ─> ```suggestion blocks
```

## Trust boundaries
- PR content is untrusted input to the model: wrapped in `<code>`, model has no tools, output only via schema.
- Model output is untrusted too: Pydantic validation, window check, line-number-prefix and fence check, gate, HTML/@-mention stripping, dynamic code-fence length.
- Supply chain: hash-locked Python deps (`requirements.lock`, 14-day cooldown at lock time), SHA-256 for the llama.cpp binary and the model (commit-pinned URL), full-SHA pins for third-party actions. Mismatch = fail closed.
- The LLM client refuses any non-loopback URL.

## Grammar vs. correctness
A GBNF/JSON-schema grammar guarantees syntax only. It cannot stop a model from proposing a wrong but well-formed patch. That is the job of the gate and of the 1-click human review.

## Cache
`~/.vibeci` (venv, llama.cpp, model) is one `actions/cache` entry keyed by the hash of both lock files. Default cache quota is 10 GB per repo (larger on paid quota since 2025-11), entries unused for 7 days are evicted; a ~4.7 GB model fits once.

## Fork PRs
Add a second workflow triggered by `workflow_run` on VibeCI completion that downloads the `vibeci-review` artifact and runs `python -m vibeci post`. Never use `pull_request_target` to check out and run PR code.

## Known limits (v0.1)
- Not yet run against a real model in CI: the e2e tests use a mock llama-server speaking the Anthropic and OpenAI shapes. Run `vibeci doctor` and the `benchmark` workflow first.
- `anthropic` transport relies on llama-server honoring forced tool choice; `doctor` probes it. If it fails, use `transport: grammar`.
- Trivy runs outside the fast path (DB download); the parser exists, the pinned install step is on the roadmap.
