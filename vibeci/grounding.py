"""Evidence grounding for anything published by the review bot.

A JSON schema or GBNF grammar guarantees shape, not truth. This module makes
the published part of a suggestion *deterministic*: every statement that
reaches GitHub is copied from a scanner finding, an exact source line, a
deterministic gate result or a SHA-256 digest. Free-form model prose is never
published as a fact. If a statement does not resolve against the evidence
ledger, it is rejected and no suggestion is emitted.

This does not claim that a model cannot be wrong internally. It claims that an
unverified model claim cannot become a published repository statement. The
model may still propose a line range and replacement; those are checked by the
patch gate before they enter this module.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import Finding

StatementKind = Literal["finding", "source_line", "gate", "digest"]
_MAX_SOURCE_STATEMENTS = 12


class GroundedStatement(BaseModel):
    """One statement whose value must resolve to recorded evidence."""

    model_config = ConfigDict(extra="forbid")

    kind: StatementKind
    ref: str = Field(min_length=1, max_length=300)
    value: str = Field(min_length=1, max_length=600)

    def render(self) -> str:
        """Render only the deterministic evidence, never an interpretation."""
        if self.kind == "source_line":
            return f"`{self.ref}` = {self.value.strip()}"
        if self.kind == "digest":
            return f"`{self.ref}`"
        if self.kind == "gate":
            return f"gate `{self.ref}`: {self.value}"
        return f"`{self.ref}`: {self.value}"


@dataclass(frozen=True)
class EvidenceLedger:
    """The only facts a published statement may refer to."""

    finding_ref: str
    finding_message: str
    path: str
    source_lines: Mapping[int, str]
    digest: str
    checks: frozenset[str] = field(default_factory=frozenset)

    def resolve(self, statement: GroundedStatement) -> bool:
        if statement.kind == "finding":
            return statement.ref == self.finding_ref and statement.value == self.finding_message
        if statement.kind == "digest":
            return statement.ref == f"sha256:{self.digest}" and statement.value == self.digest
        if statement.kind == "gate":
            return statement.ref in self.checks and statement.value == statement.ref
        if statement.kind == "source_line":
            prefix = f"{self.path}:"
            if not statement.ref.startswith(prefix):
                return False
            try:
                line = int(statement.ref[len(prefix) :])
            except ValueError:
                return False
            return self.source_lines.get(line) == statement.value
        return False


@dataclass(frozen=True)
class GroundingReport:
    ok: bool
    statements: tuple[GroundedStatement, ...]
    rejected: tuple[str, ...]
    digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "statements": [statement.model_dump() for statement in self.statements],
            "rejected": list(self.rejected),
            "sha256": self.digest,
        }


def source_digest(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def ledger_for(
    finding: Finding,
    source: str,
    lines: Sequence[int],
    checks: Sequence[str] = (),
) -> EvidenceLedger:
    """Build a ledger from deterministic inputs only."""
    split = source.splitlines()
    selected = {
        number: split[number - 1][:600]
        for number in lines
        if 1 <= number <= len(split)
    }
    return EvidenceLedger(
        finding_ref=f"{finding.tool}/{finding.rule}",
        finding_message=finding.message[:600],
        path=finding.path,
        source_lines=selected,
        digest=source_digest(source),
        checks=frozenset(checks),
    )


def build_grounding(
    finding: Finding,
    source: str,
    lines: Sequence[int],
    gate_reason: str,
) -> GroundingReport:
    """Create evidence statements and verify them before publication."""
    reason = " ".join(gate_reason.split())[:120] or "ok"
    ledger = ledger_for(finding, source, lines, [f"gate:{reason}"])
    statements = [
        GroundedStatement(kind="finding", ref=ledger.finding_ref, value=ledger.finding_message),
        GroundedStatement(kind="gate", ref=f"gate:{reason}", value=f"gate:{reason}"),
        GroundedStatement(kind="digest", ref=f"sha256:{ledger.digest}", value=ledger.digest),
    ]
    for number in lines[:_MAX_SOURCE_STATEMENTS]:
        if number in ledger.source_lines:
            statements.append(
                GroundedStatement(
                    kind="source_line",
                    ref=f"{finding.path}:{number}",
                    value=ledger.source_lines[number],
                )
            )
    return verify_grounding(statements, ledger)


def verify_grounding(statements: Sequence[GroundedStatement], ledger: EvidenceLedger) -> GroundingReport:
    """Fail closed: one unresolved statement invalidates the whole report."""
    accepted: list[GroundedStatement] = []
    rejected: list[str] = []
    counts = Counter(statement.ref for statement in statements)
    for statement in statements:
        if counts[statement.ref] > 1 or not ledger.resolve(statement):
            rejected.append(f"{statement.kind}:{statement.ref}")
        else:
            accepted.append(statement)
    canonical = json.dumps(
        [statement.model_dump() for statement in accepted], sort_keys=True, separators=(",", ":")
    )
    return GroundingReport(
        ok=not rejected and bool(accepted),
        statements=tuple(accepted),
        rejected=tuple(rejected),
        digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def render_grounding(report: GroundingReport) -> tuple[str, list[str], str | None]:
    """Return deterministic detail text, evidence refs and provenance digest."""
    if not report.ok:
        return "No verified evidence was available; no model statement was published.", [], None
    refs = [statement.ref for statement in report.statements if statement.kind != "digest"]
    detail_lines = [statement.render() for statement in report.statements if statement.kind != "digest"]
    return "Verified evidence:\n" + "\n".join(detail_lines), refs[:8], report.digest
