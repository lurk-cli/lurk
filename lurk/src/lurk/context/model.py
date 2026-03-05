"""Context model — the central bounded data structure."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .agents import AgentRegistry
from .project import ProjectGraph
from .session import CompactSession, SessionTracker
from .snapshot import CurrentSnapshot, MonitorState

logger = logging.getLogger("lurk.context")


class ContextModel:
    """
    The central context model. Bounded, in-memory.

    Updated continuously from enriched events.
    Read by MCP/HTTP servers to serve context to AI tools.
    """

    def __init__(self, stale_timeout: float = 600.0) -> None:
        self.now = CurrentSnapshot()
        self.session_tracker = SessionTracker()
        self.projects = ProjectGraph()
        self.agents = AgentRegistry(stale_timeout=stale_timeout)

    @property
    def session(self):
        return self.session_tracker.current_session

    @property
    def recent_sessions(self):
        return self.session_tracker.recent_sessions

    def process_enriched_event(self, event: dict[str, Any]) -> None:
        """Process an enriched event and update all model components."""
        self.now.update_from_enriched(event)
        self.session_tracker.process_event(event)
        self.projects.update(event)
        self.agents.process_event(event)

    def process_raw_event(self, event: dict[str, Any]) -> None:
        """Process a raw event for input state and monitor updates."""
        event_type = event.get("event_type")
        data = event.get("data")

        if event_type == "input_state" and isinstance(data, dict):
            state = data.get("state", "idle")
            self.now.update_input_state(state)

        elif event_type == "monitor_state" and isinstance(data, dict):
            active_monitor = data.get("active_monitor", 0)
            windows = data.get("windows", [])
            monitors = []
            for w in windows:
                if isinstance(w, dict):
                    monitors.append(MonitorState(
                        monitor_id=w.get("monitor_id", 0),
                        app=w.get("app"),
                        title=w.get("title"),
                    ))
            self.now.update_monitors(active_monitor, monitors)

    def load_from_db(self, conn: Any) -> None:
        """Load recent state from database on startup."""
        from ..store.database import fetch_recent_enriched, fetch_recent_sessions

        # Load cross-session memory first
        try:
            saved_sessions = fetch_recent_sessions(conn, days=7, limit=50)
            for s in reversed(saved_sessions):
                self.session_tracker.recent_sessions.append(CompactSession(
                    start_time=s.get("start_ts", 0),
                    end_time=s.get("end_ts", 0),
                    projects=s.get("projects", []),
                    summary=s.get("summary"),
                ))
            if saved_sessions:
                logger.info("Loaded %d recent sessions from DB", len(saved_sessions))
        except Exception:
            logger.debug("No saved sessions found (table may not exist yet)")

        # Load recent enriched events
        events = fetch_recent_enriched(conn, hours=24, limit=200)
        for event in reversed(events):
            self.process_enriched_event(event)
        logger.info(
            "Loaded %d events from DB. Current: %s/%s",
            len(events), self.now.app, self.now.project,
        )

    def save_session(self, conn: Any) -> None:
        """Save the current session to the database (called on session boundary)."""
        from ..store.database import save_session

        session = self.session
        if not session.projects_touched and not session.files_edited:
            return

        save_session(conn, {
            "start_ts": session.start_time,
            "end_ts": time.time(),
            "duration_seconds": time.time() - session.start_time,
            "projects": session.projects_touched[:20],
            "files_edited": session.files_edited[-50:],
            "tickets": session.tickets_worked[:20],
            "tools": session.tools_used[:20],
            "context_switches": session.context_switches,
            "focus_blocks_count": len(session.focus_blocks),
            "summary": None,  # LLM can fill this later
        })
        logger.info("Saved session to DB (%.0f min, %d projects)",
                     (time.time() - session.start_time) / 60,
                     len(session.projects_touched))

    def to_dict(self) -> dict:
        return {
            "now": self.now.to_dict(),
            "session": self.session.to_dict(),
            "recent_sessions": [s.to_dict() for s in self.recent_sessions[-5:]],
            "projects": self.projects.to_dict(),
            "agents": self.agents.to_dict(),
        }
