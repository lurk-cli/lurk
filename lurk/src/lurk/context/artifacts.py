"""Artifact lifecycle tracker — tracks documents through their lifecycle."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ArtifactStatus(str, Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    IMPLEMENTED = "implemented"
    ARCHIVED = "archived"


@dataclass
class Artifact:
    name: str
    artifact_type: str  # "prd", "spec", "presentation", "spreadsheet", "document"
    status: ArtifactStatus = ArtifactStatus.DRAFT
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    status_history: list[tuple[float, str]] = field(default_factory=list)
    workflows: list[int] = field(default_factory=list)
    stakeholders: list[str] = field(default_factory=list)  # reviewers/collaborators
    edit_count: int = 0
    total_dwell_seconds: float = 0.0

    MAX_HISTORY = 20
    MAX_WORKFLOWS = 20
    MAX_STAKEHOLDERS = 20

    def transition_status(self, new_status: ArtifactStatus, ts: float | None = None) -> None:
        ts = ts or time.time()
        if new_status != self.status:
            self.status_history.append((ts, new_status.value))
            if len(self.status_history) > self.MAX_HISTORY:
                self.status_history = self.status_history[-self.MAX_HISTORY:]
            self.status = new_status
            self.updated_ts = ts

    def record_edit(self, ts: float | None = None, dwell_seconds: float = 0) -> None:
        self.edit_count += 1
        self.updated_ts = ts or time.time()
        self.total_dwell_seconds += dwell_seconds

    def add_workflow(self, workflow_id: int) -> None:
        if workflow_id not in self.workflows:
            self.workflows.append(workflow_id)
            if len(self.workflows) > self.MAX_WORKFLOWS:
                self.workflows = self.workflows[-self.MAX_WORKFLOWS:]

    def add_stakeholder(self, name: str) -> None:
        if name and name not in self.stakeholders:
            self.stakeholders.append(name)
            if len(self.stakeholders) > self.MAX_STAKEHOLDERS:
                self.stakeholders = self.stakeholders[-self.MAX_STAKEHOLDERS:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "artifact_type": self.artifact_type,
            "status": self.status.value,
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
            "status_history": [{"ts": ts, "status": s} for ts, s in self.status_history],
            "workflows": self.workflows,
            "stakeholders": self.stakeholders,
            "edit_count": self.edit_count,
            "total_dwell_seconds": round(self.total_dwell_seconds),
        }


# Infer artifact type from sub_activity or document name
_TYPE_HINTS = {
    "spreadsheet": "spreadsheet",
    "presentation": "presentation",
    "email": "document",
}

_NAME_TYPE_HINTS = {
    "prd": "prd",
    "spec": "spec",
    "rfc": "spec",
    "roadmap": "prd",
    "budget": "spreadsheet",
    "forecast": "spreadsheet",
    "revenue": "spreadsheet",
    "deck": "presentation",
    "pitch": "presentation",
    "checklist": "document",
    "plan": "prd",
}


def _infer_type(name: str, sub_activity: str = "") -> str:
    if sub_activity in _TYPE_HINTS:
        return _TYPE_HINTS[sub_activity]
    name_lower = name.lower()
    for hint, atype in _NAME_TYPE_HINTS.items():
        if hint in name_lower:
            return atype
    return "document"


class ArtifactTracker:
    """Tracks documents through their lifecycle."""

    MAX_ARTIFACTS = 100

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    def track(
        self, name: str, artifact_type: str = "",
        sub_activity: str = "", ts: float | None = None,
        workflow_id: int | None = None,
    ) -> Artifact:
        """Track a document. Creates if new, updates if existing."""
        if not name:
            raise ValueError("Artifact name required")
        key = name.lower().strip()
        if key not in self._artifacts:
            if len(self._artifacts) >= self.MAX_ARTIFACTS:
                oldest_key = min(self._artifacts, key=lambda k: self._artifacts[k].updated_ts)
                del self._artifacts[oldest_key]
            atype = artifact_type or _infer_type(name, sub_activity)
            self._artifacts[key] = Artifact(
                name=name, artifact_type=atype,
                created_ts=ts or time.time(),
                updated_ts=ts or time.time(),
            )
        artifact = self._artifacts[key]
        artifact.record_edit(ts)
        if workflow_id is not None:
            artifact.add_workflow(workflow_id)
        return artifact

    def infer_status_transition(self, name: str, signals: dict) -> None:
        """Infer and apply status transitions from activity patterns.

        Signals:
            shared: bool — doc was shared/emailed
            reviewed: bool — doc had review activity
            idle_hours: float — hours since last edit
            ticket_updated: bool — related ticket changed status
        """
        key = name.lower().strip()
        artifact = self._artifacts.get(key)
        if not artifact:
            return

        ts = signals.get("ts") or time.time()

        if signals.get("shared") and artifact.status == ArtifactStatus.DRAFT:
            artifact.transition_status(ArtifactStatus.IN_REVIEW, ts)
        elif signals.get("idle_hours", 0) > 24 and artifact.status == ArtifactStatus.IN_REVIEW:
            artifact.transition_status(ArtifactStatus.APPROVED, ts)
        elif signals.get("ticket_updated") and artifact.status == ArtifactStatus.IN_REVIEW:
            artifact.transition_status(ArtifactStatus.APPROVED, ts)

    def get_recent(self, limit: int = 10) -> list[Artifact]:
        sorted_list = sorted(self._artifacts.values(), key=lambda a: a.updated_ts, reverse=True)
        return sorted_list[:limit]

    def get_for_workflow(self, workflow_id: int) -> list[Artifact]:
        return [a for a in self._artifacts.values() if workflow_id in a.workflows]

    def get(self, name: str) -> Artifact | None:
        return self._artifacts.get(name.lower().strip())

    def to_dict(self) -> dict[str, Any]:
        recent = self.get_recent(20)
        return {
            "total": len(self._artifacts),
            "recent": [a.to_dict() for a in recent],
        }
