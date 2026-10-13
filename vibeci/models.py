"""Pydantic models. These are the single source of truth for every structure
that crosses a trust boundary (tool output -> LLM -> GitHub review).

The JSON schema exported from `FixProposal` is what llama-server turns into a
GBNF grammar. A grammar guarantees SYNTAX only; semantics are checked by
`vibeci.gate`.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"


class Finding(BaseModel):
    """One normalized result from a deterministic scanner."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    rule: str
    path: str
    line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    message: str
    severity: Severity = Severity.warning
    # A fix produced by the scanner itself (no LLM involved). Whole replacement
    # for lines [line..end_line], or None when the scanner has no fix.
    tool_fix: Optional[str] = None

    @model_validator(mode="after")
    def _order(self) -> "Finding":
        if self.end_line < self.line:
            self.end_line = self.line
        return self


class EdgeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "none_value",
        "syscall_failure",
        "race_condition",
        "resource_leak",
        "bounds",
        "encoding",
        "injection",
        "other",
    ]
    scenario: str = Field(min_length=3, max_length=240)


class FixProposal(BaseModel):
    """What the model is allowed to say. Nothing else can be produced."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["fix", "no_fix", "needs_human"]
    bug: str = Field(min_length=3, max_length=240)
    edge_case: Optional[EdgeCase] = None
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    replacement: Optional[str] = Field(default=None, max_length=4000)
    confidence: Literal["high", "medium", "low"]

    @model_validator(mode="after")
    def _consistent(self) -> "FixProposal":
        if self.end_line < self.start_line:
            raise ValueError("end_line < start_line")
        if self.verdict == "fix" and self.replacement is None:
            raise ValueError("verdict=fix requires replacement")
        if self.verdict != "fix" and self.replacement is not None:
            raise ValueError("replacement only allowed with verdict=fix")
        return self


class Suggestion(BaseModel):
    """A fully validated, postable inline suggestion."""

    model_config = ConfigDict(extra="forbid")

    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    replacement: str
    title: str
    detail: str
    source: Literal["tool", "llm"]
    rule: str
    confidence: Literal["high", "medium", "low"] = "high"

    @property
    def fingerprint(self) -> str:
        import hashlib

        raw = f"{self.path}:{self.start_line}:{self.end_line}:{self.rule}:{self.replacement}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


def fix_schema() -> dict:
    """JSON schema handed to llama-server (-> GBNF) and to the tool-use API."""
    schema = FixProposal.model_json_schema()
    schema.pop("title", None)
    return schema
