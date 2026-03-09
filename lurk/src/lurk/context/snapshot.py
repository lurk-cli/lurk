"""Current context snapshot — what the user is doing right now."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


# Apps that should not be considered primary work activities
_BREAK_APPS = frozenset({
    # Video/streaming
    "youtube", "netflix", "hulu", "disney+", "prime video", "twitch",
    "vlc", "iina", "plex", "hbo max", "apple tv",
    # Gaming
    "steam", "epic games", "battle.net", "origin", "gog galaxy",
    "minecraft", "league of legends", "valorant", "fortnite",
    # Social media (non-work)
    "tiktok", "instagram", "snapchat", "reddit",
    # Shopping
    "amazon shopping",
    # Music (background, not workflow-relevant)
    "spotify", "apple music", "music",
})


def _is_break_app(app: str) -> bool:
    """Check if an app is a break/entertainment app (not work-relevant)."""
    return app.lower().strip() in _BREAK_APPS


@dataclass
class MonitorState:
    """State of a single monitor."""
    monitor_id: int
    app: str | None = None
    title: str | None = None

    def to_dict(self) -> dict:
        return {"monitor_id": self.monitor_id, "app": self.app, "title": self.title}


@dataclass
class ActivityRecord:
    """A scored record of activity in a specific app/context."""
    app: str
    activity: str
    sub_activity: str | None = None
    document_name: str | None = None
    file: str | None = None
    project: str | None = None
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    last_input: float = 0  # last time keyboard input was detected here
    input_seconds: float = 0  # cumulative seconds of active input
    dwell_seconds: float = 0  # total time with this app active
    input_app: str | None = None  # which app is receiving input (from daemon attribution)

    @property
    def score(self) -> float:
        """Activity score: screen-observation-weighted, input supplements."""
        now = time.time()
        # Screen observation is primary signal (~60%)
        recency = max(0, 1 - (now - self.last_seen) / 300)  # decays over 5 min
        dwell_weight = min(1.0, self.dwell_seconds / 120)  # caps at 2 min of screen time
        # Input supplements screen observation (~20%)
        input_weight = min(1.0, self.input_seconds / 60)
        input_recency = max(0, 1 - (now - self.last_input) / 120) if self.last_input else 0
        base = (dwell_weight * 0.40 + recency * 0.30 + input_weight * 0.15 + input_recency * 0.15)
        # Penalize leisure apps — they should never be "primary" work context
        if _is_break_app(self.app):
            return base * 0.3
        return base

    @property
    def is_primary(self) -> bool:
        """True if this looks like active work, not just a glance."""
        if _is_break_app(self.app):
            return False
        return self.dwell_seconds > 15 or self.input_seconds > 5

    def label(self) -> str:
        """Human-readable label for this activity."""
        if self.document_name:
            return self.document_name
        if self.file and self.project:
            return f"{self.file} ({self.project})"
        if self.file:
            return self.file
        return self.app


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
    document_name: str | None = None
    intent: str | None = None
    duration_seconds: float = 0
    interruptibility: str = "high"
    input_state: str = "idle"
    input_app: str | None = None  # which app is receiving input
    active_monitor: int = 0
    monitors: list[MonitorState] = field(default_factory=list)
    tools_active: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    _activity_start: float = field(default_factory=time.time, repr=False)
    # Activity tracking — scored records of what user is actually doing
    _activity_ring: list[ActivityRecord] = field(default_factory=list, repr=False)
    _max_ring_size: int = field(default=20, repr=False)

    def update_from_enriched(self, event: dict) -> None:
        """Update snapshot from an enriched event."""
        new_app = event.get("app", "")
        new_activity = event.get("activity", "unknown")
        ts = event.get("ts", time.time())

        # Track activity duration
        if new_app != self.app or new_activity != self.activity:
            self._activity_start = ts

        self.app = new_app
        self.file = event.get("file") or self.file
        self.project = event.get("project") or self.project
        self.language = event.get("language") or self.language
        self.ticket = event.get("ticket") or self.ticket
        self.branch = event.get("branch") or self.branch
        self.activity = new_activity
        self.sub_activity = event.get("sub_activity")
        self.document_name = event.get("document_name") or self.document_name
        self.intent = event.get("intent") or self.intent
        self.interruptibility = event.get("interruptibility", "medium")
        self.duration_seconds = (ts - self._activity_start)
        self.updated_at = ts

        # Track active tools
        if new_app and new_app not in self.tools_active:
            self.tools_active.append(new_app)
            if len(self.tools_active) > 10:
                self.tools_active = self.tools_active[-10:]

        # Update activity ring
        self._update_activity_ring(event)

    def _update_activity_ring(self, event: dict, app: str | None = None) -> None:
        """Track scored activity records for primary vs reference detection."""
        app = app or event.get("app", "")
        activity = event.get("activity", "unknown")
        ts = event.get("ts", time.time())
        input_state = event.get("input_state", self.input_state)

        # Find or create record for this app+context
        key_file = event.get("file")
        key_doc = event.get("document_name")
        record = None
        for r in self._activity_ring:
            if r.app == app and r.file == key_file and r.document_name == key_doc:
                record = r
                break

        if record is None:
            record = ActivityRecord(
                app=app,
                activity=activity,
                sub_activity=event.get("sub_activity"),
                document_name=key_doc,
                file=key_file,
                project=event.get("project"),
                first_seen=ts,
                last_seen=ts,
            )
            self._activity_ring.append(record)
            # Prune old entries
            if len(self._activity_ring) > self._max_ring_size:
                # Remove lowest-scored, oldest entries
                self._activity_ring.sort(key=lambda r: r.score, reverse=True)
                self._activity_ring = self._activity_ring[:self._max_ring_size]

        # Update timing
        gap = ts - record.last_seen
        record.last_seen = ts
        record.activity = activity
        if gap < 10:  # continuous observation
            record.dwell_seconds += gap

        # Track input with app attribution
        if input_state == "typing":
            record.last_input = ts
            if gap < 10:
                record.input_seconds += gap
            # Tag which app is receiving input
            if hasattr(self, 'input_app') and self.input_app:
                record.input_app = self.input_app

    def record_extension_input(self, app_hint: str, ts: float | None = None) -> None:
        """Record keyboard input detected by the browser extension."""
        ts = ts or time.time()
        # Find the most recent record matching this app hint
        for r in reversed(self._activity_ring):
            if app_hint.lower() in r.app.lower() or app_hint.lower() in (r.document_name or "").lower():
                r.last_input = ts
                r.input_seconds += 3  # extension reports every ~3s
                return
        # No match — create a new record
        self._activity_ring.append(ActivityRecord(
            app=app_hint,
            activity="interacting",
            first_seen=ts,
            last_seen=ts,
            last_input=ts,
            input_seconds=3,
        ))

    def get_primary_activity(self) -> ActivityRecord | None:
        """Get the highest-scored activity — what the user is actually doing."""
        if not self._activity_ring:
            return None
        # Filter to recent (last 5 min)
        now = time.time()
        recent = [r for r in self._activity_ring if now - r.last_seen < 300]
        if not recent:
            return None
        return max(recent, key=lambda r: r.score)

    def get_reference_activities(self, limit: int = 5) -> list[ActivityRecord]:
        """Get secondary activities — things open for reference, not primary work."""
        primary = self.get_primary_activity()
        now = time.time()
        recent = [r for r in self._activity_ring if now - r.last_seen < 300]
        refs = [r for r in recent if r is not primary and r.is_primary and not _is_break_app(r.app)]
        refs.sort(key=lambda r: r.last_seen, reverse=True)
        return refs[:limit]

    def update_input_state(self, state: str, app: str | None = None) -> None:
        self.input_state = state
        self.input_app = app  # which app is receiving input

    def update_monitors(self, active_monitor: int, monitors: list[MonitorState]) -> None:
        self.active_monitor = active_monitor
        self.monitors = monitors

    def to_dict(self) -> dict:
        primary = self.get_primary_activity()
        refs = self.get_reference_activities()
        return {
            "app": self.app,
            "file": self.file,
            "project": self.project,
            "language": self.language,
            "ticket": self.ticket,
            "branch": self.branch,
            "activity": self.activity,
            "sub_activity": self.sub_activity,
            "document_name": self.document_name,
            "intent": self.intent,
            "duration_seconds": round(self.duration_seconds),
            "interruptibility": self.interruptibility,
            "input_state": self.input_state,
            "input_app": self.input_app,
            "active_monitor": self.active_monitor,
            "monitors": [m.to_dict() for m in self.monitors],
            "tools_active": self.tools_active,
            "updated_at": self.updated_at,
            "primary_activity": {
                "label": primary.label(),
                "app": primary.app,
                "activity": primary.activity,
                "input_seconds": round(primary.input_seconds),
                "score": round(primary.score, 2),
            } if primary else None,
            "reference_context": [
                {"label": r.label(), "app": r.app, "last_seen": round(r.last_seen)}
                for r in refs
            ] if refs else [],
        }
