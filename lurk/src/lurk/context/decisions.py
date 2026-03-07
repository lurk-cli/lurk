"""Decision detector — infers decisions from activity pattern sequences."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InferredDecision:
    description: str
    confidence: float
    ts: float
    source_events: list[str] = field(default_factory=list)
    workflow_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "confidence": self.confidence,
            "ts": self.ts,
            "source_events": self.source_events,
            "workflow_id": self.workflow_id,
        }


class DecisionDetector:
    """Infers decisions from activity pattern sequences.

    Detects patterns like:
    - Slack discussion -> Linear status update = "Updated {ticket} after discussion"
    - Meeting -> document edit within 15min = "Updated '{doc}' after meeting '{meeting}'"
    - Rapid ticket triage (3+ tickets in 5min) = "Sprint planning / triage session"
    - Doc edit -> email send = "Shared '{doc}' for review"
    """

    MAX_DECISIONS = 100
    MAX_RECENT_EVENTS = 50
    PATTERN_WINDOW = 900  # 15 minutes

    def __init__(self) -> None:
        self._decisions: list[InferredDecision] = []
        self._recent_events: list[dict] = []
        self._last_meeting: dict | None = None
        self._ticket_burst: list[dict] = []

    def process_event(self, event: dict) -> InferredDecision | None:
        """Process an enriched event and detect decision patterns."""
        ts = event.get("ts", time.time())
        activity = event.get("activity", "")
        sub_activity = event.get("sub_activity", "")
        topic = event.get("topic", "")
        ticket = event.get("ticket", "")
        document_name = event.get("document_name", "")
        app = event.get("app", "")

        self._recent_events.append(event)
        if len(self._recent_events) > self.MAX_RECENT_EVENTS:
            self._recent_events = self._recent_events[-self.MAX_RECENT_EVENTS:]

        decision = None

        # Pattern: Meeting -> document edit within 15 min
        if activity == "meeting":
            self._last_meeting = {"title": topic or document_name or "meeting", "ts": ts}
        elif (
            self._last_meeting
            and document_name
            and activity in ("writing", "planning", "coding")
            and ts - self._last_meeting["ts"] < self.PATTERN_WINDOW
        ):
            decision = InferredDecision(
                description=f"Updated '{document_name}' after meeting '{self._last_meeting['title']}'",
                confidence=0.6,
                ts=ts,
                source_events=[f"meeting:{self._last_meeting['title']}", f"edit:{document_name}"],
            )
            self._last_meeting = None

        # Pattern: Slack discussion -> Linear/ticket update
        if not decision and ticket and activity == "planning":
            slack_events = [
                e for e in self._recent_events[-10:]
                if e.get("activity") == "communicating"
                and e.get("ts", 0) > ts - self.PATTERN_WINDOW
            ]
            if slack_events:
                slack_topic = slack_events[-1].get("topic", "discussion")
                decision = InferredDecision(
                    description=f"Updated {ticket} after discussion about '{slack_topic}'",
                    confidence=0.7,
                    ts=ts,
                    source_events=[f"discussion:{slack_topic}", f"ticket:{ticket}"],
                )

        # Pattern: Rapid ticket triage (3+ tickets in 5 min)
        if ticket:
            self._ticket_burst.append({"ticket": ticket, "ts": ts})
            self._ticket_burst = [t for t in self._ticket_burst if ts - t["ts"] < 300]
            unique_tickets = {t["ticket"] for t in self._ticket_burst}
            if len(unique_tickets) >= 3 and not decision:
                decision = InferredDecision(
                    description=f"Sprint planning / triage session ({len(unique_tickets)} tickets)",
                    confidence=0.8,
                    ts=ts,
                    source_events=[f"ticket:{t}" for t in list(unique_tickets)[:5]],
                )
                self._ticket_burst = []  # Reset after detection

        # Pattern: Doc edit -> email send
        if not decision and sub_activity in ("email_composing",) and self._recent_events:
            doc_events = [
                e for e in self._recent_events[-10:]
                if e.get("document_name")
                and e.get("ts", 0) > ts - self.PATTERN_WINDOW
            ]
            if doc_events:
                doc = doc_events[-1]["document_name"]
                decision = InferredDecision(
                    description=f"Shared '{doc}' for review",
                    confidence=0.6,
                    ts=ts,
                    source_events=[f"edit:{doc}", "email:compose"],
                )

        if decision:
            self._decisions.append(decision)
            if len(self._decisions) > self.MAX_DECISIONS:
                self._decisions = self._decisions[-self.MAX_DECISIONS:]

        return decision

    def get_recent(self, hours: float = 4, limit: int = 10) -> list[InferredDecision]:
        cutoff = time.time() - hours * 3600
        recent = [d for d in self._decisions if d.ts > cutoff]
        return recent[-limit:]

    def get_for_workflow(self, workflow_id: int) -> list[InferredDecision]:
        return [d for d in self._decisions if d.workflow_id == workflow_id]

    def to_dict(self) -> dict[str, Any]:
        recent = self.get_recent()
        return {
            "total": len(self._decisions),
            "recent": [d.to_dict() for d in recent],
        }
