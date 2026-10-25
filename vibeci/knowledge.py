"""Conflict-free, local-only knowledge journals and bounded context compaction."""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .safety import redact_sensitive_text

_NOTE_KINDS = {"decision", "evidence", "handoff", "observation", "warning"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compact_notes(
    entries: list[dict[str, Any]],
    *,
    max_chars: int,
    max_entries: int,
    preserve_kinds: set[str],
) -> dict[str, Any]:
    """Select bounded notes and fingerprint everything omitted.

    Full source notes remain untouched on disk. Warnings and decisions are
    considered before ordinary notes, then recency decides within each group.
    """
    if max_chars < 500 or max_entries < 1:
        raise ValueError("context budget must allow at least 500 characters and one entry")
    indexed = list(enumerate(entries))
    priority = sorted(
        indexed,
        key=lambda item: (item[1].get("kind") in preserve_kinds, item[1].get("created_at", ""), item[0]),
        reverse=True,
    )
    selected_indexes: set[int] = set()
    used = 300
    for index, entry in priority:
        encoded_length = len(_canonical(entry)) + 1
        if len(selected_indexes) >= max_entries or used + encoded_length > max_chars:
            continue
        selected_indexes.add(index)
        used += encoded_length
    selected = [entry for index, entry in indexed if index in selected_indexes]
    omitted = [entry for index, entry in indexed if index not in selected_indexes]
    omitted_digest = hashlib.sha256(_canonical(omitted).encode()).hexdigest() if omitted else None
    return {
        "entries": selected,
        "total_entries": len(entries),
        "omitted_count": len(omitted),
        "omitted_sha256": omitted_digest,
    }


class KnowledgeJournal:
    """Append immutable per-team notes without a shared writable document."""

    def __init__(
        self,
        repository_root: Path,
        relative_directory: str,
        teams: set[str],
        maximum_note_chars: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        candidate = (self.repository_root / relative_directory).resolve()
        if candidate != self.repository_root and self.repository_root not in candidate.parents:
            raise ValueError("knowledge directory must stay inside the repository")
        self.root = candidate
        self.teams = teams
        self.maximum_note_chars = maximum_note_chars
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def append(self, team: str, kind: str, text: str, evidence: list[str]) -> dict[str, Any]:
        if team not in self.teams:
            raise ValueError(f"unknown expert team: {team}")
        if kind not in _NOTE_KINDS:
            raise ValueError(f"note kind must be one of {sorted(_NOTE_KINDS)}")
        clean_text = " ".join(text.strip().split())
        if not clean_text or len(clean_text) > self.maximum_note_chars:
            raise ValueError(f"note text must contain 1-{self.maximum_note_chars} characters")
        if redact_sensitive_text(clean_text) != clean_text:
            raise ValueError("knowledge notes cannot contain credential-like data")
        clean_evidence = list(dict.fromkeys(" ".join(item.strip().split()) for item in evidence if item.strip()))
        if not clean_evidence or len(clean_evidence) > 12 or any(len(item) > 500 for item in clean_evidence):
            raise ValueError("a note requires 1-12 bounded evidence references")
        if any(redact_sensitive_text(item) != item for item in clean_evidence):
            raise ValueError("knowledge evidence cannot contain credential-like data")
        now = self.clock().astimezone(timezone.utc)
        created_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        payload = {
            "version": 1,
            "team": team,
            "kind": kind,
            "created_at": created_at,
            "text": clean_text,
            "evidence": clean_evidence,
        }
        payload["note_id"] = "note-" + hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]
        team_dir = self.root / "teams" / team
        team_dir.mkdir(parents=True, exist_ok=True)
        resolved_team = team_dir.resolve()
        if self.root not in resolved_team.parents:
            raise ValueError("team knowledge directory escaped its configured root")
        timestamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        target = team_dir / f"{timestamp}-{payload['note_id']}.json"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return payload

    def entries(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not self.root.exists():
            return records
        for path in sorted((self.root / "teams").glob("*/*.json")):
            if path.is_symlink() or path.stat().st_size > self.maximum_note_chars * 4:
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(value, dict)
                and value.get("team") in self.teams
                and value.get("kind") in _NOTE_KINDS
                and isinstance(value.get("text"), str)
                and isinstance(value.get("evidence"), list)
            ):
                records.append(value)
        return records

    def context(self, *, max_chars: int, max_entries: int, preserve_kinds: set[str]) -> dict[str, Any]:
        return compact_notes(
            self.entries(),
            max_chars=max_chars,
            max_entries=max_entries,
            preserve_kinds=preserve_kinds,
        )
