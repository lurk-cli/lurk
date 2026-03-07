"""Stakeholder graph — tracks people the user interacts with."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Stakeholder:
    name: str
    interactions: int = 0
    last_seen: float = 0.0
    contexts: list[str] = field(default_factory=list)  # "meeting", "email", "slack", "doc_reviewer"
    workflows: list[int] = field(default_factory=list)  # workflow IDs where this person appeared

    MAX_CONTEXTS = 10
    MAX_WORKFLOWS = 20

    def record_interaction(self, context: str, workflow_id: int | None = None, ts: float | None = None) -> None:
        self.interactions += 1
        self.last_seen = ts or time.time()
        if context and context not in self.contexts:
            self.contexts.append(context)
            if len(self.contexts) > self.MAX_CONTEXTS:
                self.contexts = self.contexts[-self.MAX_CONTEXTS:]
        if workflow_id is not None and workflow_id not in self.workflows:
            self.workflows.append(workflow_id)
            if len(self.workflows) > self.MAX_WORKFLOWS:
                self.workflows = self.workflows[-self.MAX_WORKFLOWS:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "interactions": self.interactions,
            "last_seen": self.last_seen,
            "contexts": self.contexts,
            "workflows": self.workflows,
        }


class StakeholderGraph:
    """Tracks people the PM interacts with across meetings, emails, slack, docs."""

    MAX_STAKEHOLDERS = 100

    def __init__(self) -> None:
        self._stakeholders: dict[str, Stakeholder] = {}

    def record(
        self, name: str, context: str,
        workflow_id: int | None = None, ts: float | None = None,
    ) -> None:
        """Record an interaction with a person."""
        if not name or not name.strip():
            return
        name = name.strip()
        key = name.lower()
        if key not in self._stakeholders:
            if len(self._stakeholders) >= self.MAX_STAKEHOLDERS:
                # Evict least recently seen
                oldest_key = min(self._stakeholders, key=lambda k: self._stakeholders[k].last_seen)
                del self._stakeholders[oldest_key]
            self._stakeholders[key] = Stakeholder(name=name)
        self._stakeholders[key].record_interaction(context, workflow_id, ts)

    def get_recent(self, limit: int = 10) -> list[Stakeholder]:
        """Get most recently interacted stakeholders."""
        sorted_list = sorted(self._stakeholders.values(), key=lambda s: s.last_seen, reverse=True)
        return sorted_list[:limit]

    def get_for_workflow(self, workflow_id: int) -> list[Stakeholder]:
        """Get stakeholders involved in a specific workflow."""
        return [
            s for s in self._stakeholders.values()
            if workflow_id in s.workflows
        ]

    def to_dict(self) -> dict[str, Any]:
        recent = self.get_recent(20)
        return {
            "total": len(self._stakeholders),
            "recent": [s.to_dict() for s in recent],
        }
