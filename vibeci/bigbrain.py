"""Big Brain questions, notifications, team briefs and local knowledge commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .agent_types import CommandResult
from .knowledge import KnowledgeJournal
from .models import AgentNotification, AgentState, BlockingQuestion
from .orchestration import TeamDefinition, build_subagent_brief
from .safety import RiskSignal, classify_request, redact_sensitive_text


class BigBrainCommands:
    """Mixin for deterministic orchestration; it never launches a provider."""

    settings: dict[str, Any]
    teams: dict[str, TeamDefinition]
    knowledge: KnowledgeJournal
    clock: Callable[[], datetime]

    def _timestamp(self) -> str:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("agent clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _emit(
        self,
        state: AgentState,
        level: str,
        title: str,
        message: str,
        link: str | None = None,
    ) -> AgentNotification:
        maximum = int(self.settings["notifications"]["maximum_records"])
        if len(state.notifications) >= maximum:
            removable = next(
                (
                    index
                    for index, item in enumerate(state.notifications)
                    if item.acknowledged or item.level == "notice"
                ),
                None,
            )
            if removable is None:
                raise ValueError("notification capacity is full; acknowledge an existing warning or error")
            state.notifications.pop(removable)
        created_at = self._timestamp()
        identity = json.dumps(
            [created_at, level, title, message, link, len(state.notifications)],
            separators=(",", ":"),
        )
        notice = AgentNotification(
            notification_id="n-" + hashlib.sha256(identity.encode()).hexdigest()[:12],
            level=level,
            title=title,
            message=message,
            link=link,
            created_at=created_at,
        )
        state.notifications.append(notice)
        return notice

    def _record_question(
        self,
        state: AgentState,
        team: str,
        prompt: str,
        trigger_codes: list[str],
        context: list[str],
    ) -> BlockingQuestion:
        if redact_sensitive_text(prompt) != prompt or any(
            redact_sensitive_text(item) != item for item in context
        ):
            raise ValueError("blocking questions cannot persist credential-like data")
        if team not in self.teams:
            raise ValueError(f"unknown expert team: {team}")
        for existing in state.questions:
            if existing.status == "open" and existing.team == team and existing.prompt == prompt:
                return existing
        maximum = int(self.settings["orchestration"]["maximum_open_questions"])
        if sum(question.status == "open" for question in state.questions) >= maximum:
            raise ValueError("open-question limit reached; answer an existing question before adding another")
        if len(state.questions) >= 20:
            answered = next(
                (index for index, question in enumerate(state.questions) if question.status == "answered"), None
            )
            if answered is None:
                raise ValueError("question history is full")
            state.questions.pop(answered)
        created_at = self._timestamp()
        identity = json.dumps([created_at, team, prompt, trigger_codes, context], separators=(",", ":"))
        question = BlockingQuestion(
            question_id="q-" + hashlib.sha256(identity.encode()).hexdigest()[:12],
            team=team,
            prompt=prompt,
            trigger_codes=trigger_codes,
            context=context,
            created_at=created_at,
        )
        state.questions.append(question)
        self._emit(
            state,
            "warning",
            "Human clarification required",
            f"{question.question_id} blocks worker starts for {team}: {question.prompt}",
        )
        return question

    @staticmethod
    def _open_questions(state: AgentState) -> list[BlockingQuestion]:
        return [question for question in state.questions if question.status == "open"]

    def _require_no_open_questions(self, state: AgentState) -> None:
        open_ids = [question.question_id for question in self._open_questions(state)]
        if open_ids and self.settings["orchestration"]["block_workers_on_open_questions"]:
            raise ValueError(f"blocking human questions must be answered first: {open_ids}")

    @staticmethod
    def _risk_prompt(signals: list[RiskSignal]) -> str:
        questions = " ".join(dict.fromkeys(signal.question for signal in signals))
        return questions[:800]

    @staticmethod
    def _text_and_references(tokens: list[str], usage: str) -> tuple[str, list[str]]:
        text: list[str] = []
        references: list[str] = []
        index = 0
        seen_reference = False
        while index < len(tokens):
            if tokens[index] == "--ref":
                seen_reference = True
                if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
                    raise ValueError(usage)
                references.append(tokens[index + 1])
                index += 2
                continue
            if tokens[index].startswith("--"):
                raise ValueError(f"unknown option: {tokens[index]}")
            if seen_reference:
                raise ValueError(usage)
            text.append(tokens[index])
            index += 1
        clean = " ".join(text).strip()
        if not clean:
            raise ValueError(usage)
        return clean, references

    def _cmd_ask(self, state: AgentState, args: list[str]) -> CommandResult:
        usage = 'usage: /ask <team> -- "<question>" [--ref <context>]'
        if len(args) < 3 or args[1] != "--":
            raise ValueError(usage)
        question_text, references = self._text_and_references(args[2:], usage)
        question = self._record_question(state, args[0].casefold(), question_text, ["HUMAN_QUESTION"], references)
        return CommandResult(
            True,
            True,
            f"Question {question.question_id} recorded. Managed worker starts are paused until /answer.",
            question.model_dump(),
        )

    def _cmd_answer(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) < 3 or args[1] != "--":
            raise ValueError('usage: /answer <question-id> -- "<answer>"')
        answer = " ".join(args[2:]).strip()
        if not answer or len(answer) > 1200:
            raise ValueError("question answer must contain 1-1200 characters")
        if redact_sensitive_text(answer) != answer:
            raise ValueError("question answers cannot contain credential-like data")
        question = next((item for item in state.questions if item.question_id == args[0]), None)
        if question is None:
            raise ValueError(f"unknown question: {args[0]}")
        if question.status != "open":
            raise ValueError(f"question {question.question_id} is already answered")
        question.status = "answered"
        question.answer = answer
        question.answered_at = self._timestamp()
        self._emit(
            state,
            "notice",
            "Human clarification recorded",
            f"{question.question_id} was answered. Re-submit or re-plan the request; policy checks still apply.",
        )
        return CommandResult(
            True,
            True,
            f"Answer recorded for {question.question_id}. This does not override policy or approve a plan.",
            question.model_dump(),
        )

    def _cmd_teams(self, _state: AgentState, args: list[str]) -> CommandResult:
        if args:
            raise ValueError("/teams takes no arguments")
        data = {team_id: team.as_dict() for team_id, team in sorted(self.teams.items())}
        return CommandResult(True, False, json.dumps(data, indent=2), {"teams": data})

    def _cmd_brief(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) < 3 or args[1] != "--":
            raise ValueError('usage: /brief <team> -- "<bounded request>"')
        team_id = args[0].casefold()
        if team_id not in self.teams:
            raise ValueError(f"unknown expert team: {team_id}")
        request = " ".join(args[2:]).strip()
        if redact_sensitive_text(request) != request:
            raise ValueError("subagent briefs cannot contain credential-like data")
        signals = classify_request(request)
        if signals:
            question = self._record_question(
                state,
                "security",
                self._risk_prompt(signals),
                [signal.code for signal in signals],
                [signal.evidence for signal in signals],
            )
            return CommandResult(
                False,
                True,
                f"Brief was not created. Security question {question.question_id} requires human clarification.",
                {"question": question.model_dump(), "signals": [signal.as_dict() for signal in signals]},
            )
        context_settings = self.settings["context"]
        context = self.knowledge.context(
            max_chars=int(context_settings["maximum_brief_chars"]),
            max_entries=int(context_settings["maximum_note_entries"]),
            preserve_kinds=set(context_settings["preserve_note_kinds"]),
        )
        packet = build_subagent_brief(
            self.teams[team_id],
            request,
            baseline_sha=state.baseline_sha,
            goal_id=state.goal.goal_id if state.goal else None,
            open_questions=[question.model_dump() for question in self._open_questions(state)],
            knowledge_context=context,
            created_at=self.clock(),
        )
        return CommandResult(
            True,
            False,
            json.dumps(packet, indent=2),
            packet,
        )

    def _cmd_note(self, state: AgentState, args: list[str]) -> CommandResult:
        usage = 'usage: /note <team> <kind> -- "<text>" --ref <evidence>'
        if len(args) < 5 or args[2] != "--":
            raise ValueError(usage)
        text, references = self._text_and_references(args[3:], usage)
        if not references:
            raise ValueError("knowledge notes require at least one --ref evidence value")
        signals = classify_request(text)
        if signals:
            question = self._record_question(
                state,
                "security",
                self._risk_prompt(signals),
                [signal.code for signal in signals],
                [signal.evidence for signal in signals],
            )
            return CommandResult(
                False,
                True,
                f"Note was not recorded. Security question {question.question_id} requires clarification.",
                question.model_dump(),
            )
        note = self.knowledge.append(args[0].casefold(), args[1].casefold(), text, references)
        return CommandResult(
            True, True, f"Local knowledge note {note['note_id']} recorded at {note['created_at']}.", note
        )

    def _cmd_context(self, _state: AgentState, args: list[str]) -> CommandResult:
        if args:
            raise ValueError("/context takes no arguments")
        context_settings = self.settings["context"]
        data = self.knowledge.context(
            max_chars=int(context_settings["maximum_brief_chars"]),
            max_entries=int(context_settings["maximum_note_entries"]),
            preserve_kinds=set(context_settings["preserve_note_kinds"]),
        )
        return CommandResult(True, False, json.dumps(data, indent=2), data)

    def _cmd_notifications(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) > 1 or (args and args[0].casefold() not in {"all", "open"}):
            raise ValueError("usage: /notifications [all|open]")
        include_all = bool(args and args[0].casefold() == "all")
        selected = [item for item in state.notifications if include_all or not item.acknowledged]
        data = [item.model_dump() for item in selected]
        return CommandResult(True, False, json.dumps(data, indent=2), {"notifications": data})

    def _cmd_ack(self, state: AgentState, args: list[str]) -> CommandResult:
        if len(args) != 1:
            raise ValueError("usage: /ack <notification-id>")
        notice = next((item for item in state.notifications if item.notification_id == args[0]), None)
        if notice is None:
            raise ValueError(f"unknown notification: {args[0]}")
        if notice.acknowledged:
            raise ValueError(f"notification {notice.notification_id} is already acknowledged")
        notice.acknowledged = True
        return CommandResult(True, True, f"Notification {notice.notification_id} acknowledged.", notice.model_dump())
