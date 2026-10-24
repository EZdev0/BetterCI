# Research-Dossier (Stand 2026-10-13)

Konfidenz: [SICHER] selbst geprüft (Binary, Tests, Lockfiles) · [WAHRSCHEINLICH] mehrere Quellen, nicht selbst gemessen · [UNSICHER] Einzelquelle/Ableitung.

## 1. Runner
- [WAHRSCHEINLICH] Public Repos: 4 vCPU / 16 GB RAM / ~14 GB SSD auf `ubuntu-latest`; private Repos kleiner (2 vCPU). Quelle: GitHub Docs "GitHub-hosted runners".
- [WAHRSCHEINLICH] Cache: 10 GB Standard pro Repo, seit 2025-11 darüber hinaus kostenpflichtig möglich; 7 Tage ohne Zugriff = Eviction (GitHub Changelog 2025-11-20).
- [UNSICHER] Reale CPU-Tokenraten eines 7B Q4 auf diesen Runnern: keine belastbare Messung gefunden. Ableitung aus Blog-Zahlen: Prefill eher zweistellig tok/s. Deshalb messbare Benchmark-Kommandos in `README.md` (ein `benchmark.yml` existiert noch nicht).

## 2. Lokale Modelle 2026
- [WAHRSCHEINLICH] Kleine Kandidaten: Qwen2.5-Coder-7B (Apache-2.0), Qwen3.5 4B/9B, Gemma 4 E4B, Granite 4.x. Große MoE-Coder (30B-A3B+) passen nicht in 16 GB.
- [SICHER] Gewählt: Qwen2.5-Coder-7B-Instruct Q4_K_M (bartowski-Repo, Commit + SHA-256 in `tools.lock.json`, Werte von der HF-Dateiseite; Download hier nicht testbar, Hash-Check im Workflow ist der Test). Nicht-Thinking-Modell = kein Reasoning-Overhead.

## 3. Claude-SDK-Kompatibilität (neue Methode)
- [SICHER] llama-server b11429 enthält `/v1/messages` und `/v1/messages/count_tokens` (Strings im Binary, wandelt Anthropic -> OpenAI-Chat intern). Ollama hat ebenfalls Anthropic-Kompatibilität [WAHRSCHEINLICH].
- [SICHER] Das gepinnte `anthropic`-SDK 1.9.0 hat kein `temperature`-Argument mehr; Wert geht über `extra_body`. Gefunden durch den E2E-Test.
- [WAHRSCHEINLICH] Volles Claude Code / Agent SDK gegen 7B-CPU ist ungeeignet: großer System-Prompt + Tool-Schemas, instabile Tool-Calls kleiner Modelle, Prompt-Cache-Invalidierung (Quellen: llama.cpp Discussion #14758, Blog "Claude Code, llama.cpp and the Hidden Prompt Cache Killer"). Deshalb: schlankes `anthropic`-SDK mit einem erzwungenen Tool. Agent SDK nur optional für selbst gehostete/stärkere Setups.
- [UNSICHER] Ob die Anthropic-Route bei erzwungenem Tool eine harte Grammatik anwendet: `vibeci doctor` prüft live; Fallback `transport: grammar`.

## 4. Structured Output
- [WAHRSCHEINLICH] llama.cpp wandelt `json_schema` in GBNF; Grammatik sichert nur Syntax, nicht Korrektheit. Die Forderung "physisch keine Halluzination" ist falsch; Semantik braucht Gate (hier umgesetzt).
- [SICHER] Der Python-Konverter `json_schema_to_grammar.py` existiert im aktuellen llama.cpp-Repo nicht mehr (nur C++). `grammars/fix.gbnf` ist handgeschrieben und nicht gegen die Engine validiert.

## 5. Sicherheit (kritisch)
- [WAHRSCHEINLICH] März 2026: `trivy-action`/Trivy kompromittiert (TeamPCP), ~75 Tags umgebogen, CI-Secrets abgegriffen. Konsequenz: SHA-Pins, Checksummen, kein `@v1`.
- [WAHRSCHEINLICH] Feb 2026 "Clinejection": Prompt Injection über Issue-Titel -> Cache-Poisoning -> Token-Diebstahl bei einem KI-Agenten in CI. Konsequenz: Modell ohne Tools, PR-Text als Daten, Modell-Hash nach Cache-Restore prüfen.
- [WAHRSCHEINLICH] GitHubs Actions-Security-Roadmap 2026: Dependency-Locking, Egress-Firewall, scoped secrets (teils angekündigt, Stand je Feature prüfen).
- [SICHER] Fork-PRs bekommen bei `pull_request` ein read-only Token; `pull_request_target` + Checkout von PR-Code ist der klassische Angriff.

## 6. Fertige Projekte (nutzen/ansehen, nicht blind übernehmen)
PR-Agent (The-PR-Agent/pr-agent), Gito, Kodus (kodustech/kodus-ai-cr), hampsterx/pr-reviewer-action, saifullahsaeed/pr-review-action, ai-action/ollama-action, reviewdog (Suggestions), Zizmor, kodustech/awesome-ai-code-review. [WAHRSCHEINLICH] Alle brauchen einen Modell-Endpoint oder Cloud-Key; keines kombiniert Scanner-zuerst + lokales Modell im Runner + Gate in einer Datei.

## 7. Neue Methoden, die eingebaut sind / geprüft werden
1. Scanner-Fixes ohne Modell (Ruff safe edits, zizmor `--fix=safe`).
2. Patch-Gate mit No-Regression statt nur Syntax.
3. Diff-Sichtbarkeits-Clamp (GitHub akzeptiert Suggestions nur auf Diff-Zeilen).
4. Harte Modell-Budgets (max Findings, Sekunden-Deadline).
5. [UNSICHER] `--spec-type ngram-mod` (modellfreie Speculative Decoding, passt zu "Code umschreiben"): in b11429 vorhanden, Nutzen unbewiesen; opt-in + Benchmark.
6. [UNSICHER] `--cache-reuse` + statischer System-Prompt vorn für KV-Prefix-Reuse.

## 8. Korrekturen an der Vorgabe
- Radon/Vulture/Ruff sind Python-only; C/C++ und Shell brauchen andere Scanner.
- 90 s kalt / 30 s gecached mit 7B plus LLM ist unwahrscheinlich [UNSICHER]; nur der deterministische Teil ist realistisch. Messen.
- "Eine YAML-Datei mit eingebettetem Agent" ist schwer wartbar und nicht pinbar; stattdessen Composite Action + 1 Consumer-YAML.
- 100k Stars sind kein planbares Ziel.
