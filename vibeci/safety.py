"""Deterministic request triage for high-risk or policy-bypass language.

This is deliberately small and conservative. It does not classify people or
infer intent; it identifies request text that needs a human clarification or a
security-only review before a worker can start.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RiskSignal:
    code: str
    severity: str
    evidence: str
    question: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


_PATTERNS: tuple[tuple[str, str, re.Pattern[str], str], ...] = (
    (
        "INSTRUCTION_OVERRIDE",
        "critical",
        re.compile(
            r"(?:ignore|disregard|override|forget).{0,50}(?:system|developer|policy|previous|safety)"
            r"|(?:reveal|print|leak).{0,30}(?:system prompt|hidden instructions)"
            r"|\b(?:perform|use|attempt)\s+(?:a\s+)?jailbreak\b"
            r"|\bjailbreak\s+(?:the|this)\s+(?:agent|model|system)\b",
            re.IGNORECASE,
        ),
        "The request appears to override trusted instructions. What legitimate repository outcome is required?",
    ),
    (
        "DESTRUCTIVE_REQUEST",
        "critical",
        re.compile(
            r"\brm\s+(?:-[A-Za-z]*r[A-Za-z]*f|-rf|-fr)\s+(?:/|~|\.|\$HOME)"
            r"|\b(?:delete|erase|wipe)\s+(?:all|every|the entire)\b"
            r"|\bgit\s+push\b.{0,30}\b(?:--force|-f)\b",
            re.IGNORECASE,
        ),
        "This may destroy files or shared history. Which exact paths and recovery checkpoint were approved?",
    ),
    (
        "CREDENTIAL_EXFILTRATION",
        "critical",
        re.compile(
            r"\b(?:send|upload|post|exfiltrat\w*|print|dump)\b.{0,80}"
            r"\b(?:token|secret|credential|private key|password|GITHUB_TOKEN)\b",
            re.IGNORECASE,
        ),
        "This may expose credentials. Confirm the benign diagnostic goal without requesting secret values.",
    ),
    (
        "MALWARE_CONSTRUCTION",
        "critical",
        re.compile(
            r"\b(?:build|create|write|implement|inject|deploy)\b.{0,80}"
            r"\b(?:malware|ransomware|keylogger|credential stealer|reverse shell|botnet|rootkit)\b",
            re.IGNORECASE,
        ),
        "The request may create offensive code. Should this be restricted to detection, reporting or safe test fixtures?",
    ),
    (
        "SECURITY_BYPASS",
        "high",
        re.compile(
            r"\b(?:disable|bypass|evade|turn off|skip)\b.{0,60}"
            r"\b(?:security|guardrail|permission|policy|scanner|verification|review|check)\w*\b",
            re.IGNORECASE,
        ),
        "A security or verification control may be bypassed. Why is the control insufficient, and what safer change is acceptable?",
    ),
)

_SECRET_VALUE = re.compile(
    r"(?i)\b(token|password|secret|api[_ -]?key)\s*[:=]\s*[^\s,;]+",
)
_TOKEN_LITERAL = re.compile(r"\b(?:gh[opsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_sensitive_text(text: str) -> str:
    """Redact common credential shapes before durable logs or notifications."""
    redacted = _PRIVATE_KEY.sub("<redacted-private-key>", text)
    redacted = _TOKEN_LITERAL.sub("<redacted-token>", redacted)
    return _SECRET_VALUE.sub(r"\1=<redacted>", redacted)


def _evidence(text: str, match: re.Match[str]) -> str:
    # Keep only the matched risk phrase. Surrounding text can contain the very
    # credential that triggered triage and must not be copied into durable state.
    excerpt = " ".join(text[match.start():match.end()].split())
    return redact_sensitive_text(excerpt)[:200]


def classify_request(text: str) -> list[RiskSignal]:
    """Return bounded risk signals without executing or trusting request text."""
    if not isinstance(text, str) or not text.strip():
        return []
    signals: list[RiskSignal] = []
    for code, severity, pattern, question in _PATTERNS:
        match = pattern.search(text)
        if match:
            signals.append(RiskSignal(code, severity, _evidence(text, match), question))
    return signals
