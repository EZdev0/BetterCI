"""Small shared result type for deterministic agent command handlers."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    changed: bool
    message: str
    data: dict | None = None
