"""Current context snapshot — what the user is doing right now."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class MonitorState:
    """State of a single monitor."""
    monitor_id: int
    app: str | None = None
    title: str | None = None

    def to_dict(self) -> dict:
        return {"monitor_id": self.monitor_id, "app": self.app, "title": self.title}


@dataclass
class CurrentSnapshot:
    """Real-time snapshot of what the user is doing."""
    app: str = ""
    file: str | None = None
    project: str | None = None
    language: str | None = None
    ticket: str | None = None
    branch: str | None = None
    activity: str = "idle"
    sub_activity: str | None = None
    intent: str | None = None
    duration_seconds: float = 0
    interruptibility: str = "high"
    input_state: str = "idle"
    active_monitor: int = 0
    monitors: list[MonitorState] = field(default_factory=list)
    tools_active: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    _activity_start: float = field(default_factory=time.time, repr=False)

    def update_from_enriched(self, event: dict) -> None:
        """Update snapshot from an enriched event."""
        new_app = event.get("app", "")
        new_activity = event.get("activity", "unknown")

        # Track activity duration
        if new_app != self.app or new_activity != self.activity:
            self._activity_start = event.get("ts", time.time())

        self.app = new_app
        self.file = event.get("file") or self.file
        self.project = event.get("project") or self.project
        self.language = event.get("language") or self.language
        self.ticket = event.get("ticket") or self.ticket
        self.branch = event.get("branch") or self.branch
        self.activity = new_activity
        self.sub_activity = event.get("sub_activity")
        self.intent = event.get("intent") or self.intent
        self.interruptibility = event.get("interruptibility", "medium")
        self.duration_seconds = (event.get("ts", time.time()) - self._activity_start)
        self.updated_at = event.get("ts", time.time())

        # Track active tools
        if new_app and new_app not in self.tools_active:
            self.tools_active.append(new_app)
            if len(self.tools_active) > 10:
                self.tools_active = self.tools_active[-10:]

    def update_input_state(self, state: str) -> None:
        self.input_state = state

    def update_monitors(self, active_monitor: int, monitors: list[MonitorState]) -> None:
        self.active_monitor = active_monitor
        self.monitors = monitors

    def to_dict(self) -> dict:
        return {
            "app": self.app,
            "file": self.file,
            "project": self.project,
            "language": self.language,
            "ticket": self.ticket,
            "branch": self.branch,
            "activity": self.activity,
            "sub_activity": self.sub_activity,
            "intent": self.intent,
            "duration_seconds": round(self.duration_seconds),
            "interruptibility": self.interruptibility,
            "input_state": self.input_state,
            "active_monitor": self.active_monitor,
            "monitors": [m.to_dict() for m in self.monitors],
            "tools_active": self.tools_active,
            "updated_at": self.updated_at,
        }
