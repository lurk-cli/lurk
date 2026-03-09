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
from .workflows import WorkflowClusterer
from .stakeholders import StakeholderGraph
from .artifacts import ArtifactTracker
from .decisions import DecisionDetector
from .workstreams import WorkstreamManager

logger = logging.getLogger("lurk.context")


class ContextModel:
    """
    The central context model. Bounded, in-memory.

    Updated continuously from enriched events.
    Read by MCP/HTTP servers to serve context to AI tools.
    """

    def __init__(self, stale_timeout: float = 600.0) -> None:
        self._pm_activity_counts: dict[str, int] = {}
        self._pm_mode_cache: bool | None = None
        self._pm_mode_last_check: float = 0
        self.now = CurrentSnapshot()
        self.session_tracker = SessionTracker()
        self.projects = ProjectGraph()
        self.agents = AgentRegistry(stale_timeout=stale_timeout)
        self.workflows = WorkflowClusterer()
        self.stakeholders = StakeholderGraph()
        self.artifacts = ArtifactTracker()
        self.decisions = DecisionDetector()
        self.workstreams = WorkstreamManager()
        self.workstream_engine: Any = None  # WorkstreamEngine, set externally

    @property
    def session(self):
        return self.session_tracker.current_session

    @property
    def recent_sessions(self):
        return self.session_tracker.recent_sessions

    @property
    def pm_mode_active(self) -> bool:
        """Whether PM features should be active based on config and auto-detection."""
        from ..config.settings import load_config
        config = load_config()
        mode = config.pm.mode
        if mode == "on":
            return True
        if mode == "off":
            return False
        # Auto mode: check activity distribution
        now = time.time()
        if self._pm_mode_cache is not None and now - self._pm_mode_last_check < 300:
            return self._pm_mode_cache
        self._pm_mode_last_check = now
        total = sum(self._pm_activity_counts.values())
        if total < 10:
            self._pm_mode_cache = False
            return False
        pm_activities = {"planning", "writing", "meeting", "communicating", "marketing", "sales", "support"}
        pm_count = sum(self._pm_activity_counts.get(a, 0) for a in pm_activities)
        self._pm_mode_cache = (pm_count / total) > 0.5
        return self._pm_mode_cache

    def process_enriched_event(self, event: dict[str, Any]) -> None:
        """Process an enriched event and update all model components."""
        self.now.update_from_enriched(event)
        self.session_tracker.process_event(event)
        self.projects.update(event)
        self.agents.process_event(event)
        self.workflows.process_enriched_event(event)
        self.workstreams.ingest_event(event)

        # Track activity distribution for PM auto-detection
        activity = event.get("activity", "")
        if activity:
            self._pm_activity_counts[activity] = self._pm_activity_counts.get(activity, 0) + 1
            if len(self._pm_activity_counts) > 50:
                # Keep only the most common activities
                sorted_activities = sorted(self._pm_activity_counts.items(), key=lambda x: x[1], reverse=True)
                self._pm_activity_counts = dict(sorted_activities[:30])

        # PM features: track stakeholders from calendar, artifacts from documents, decisions from patterns
        self._process_pm_features(event)

    def _process_pm_features(self, event: dict[str, Any]) -> None:
        """Process PM-specific features from enriched events."""
        ts = event.get("ts", 0)
        activity = event.get("activity", "")
        document_name = event.get("document_name")
        sub_activity = event.get("sub_activity", "")

        # Get active workflow for linking
        wf = self.workflows.get_active_workflow()
        wf_id = wf.id if wf else None

        # Track documents as artifacts
        if document_name:
            artifact = self.artifacts.track(
                name=document_name,
                sub_activity=sub_activity,
                ts=ts,
                workflow_id=wf_id,
            )
            # Infer status transitions
            if sub_activity == "email_composing":
                self.artifacts.infer_status_transition(document_name, {"shared": True, "ts": ts})
            # Link artifact to active workflow
            if wf:
                wf.add_artifact_ref(
                    document_name, artifact.artifact_type,
                    artifact.status.value, last_edit=ts,
                )

        # Extract attendees from calendar events
        data = event.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                data = None
        if isinstance(data, dict) and activity == "meeting":
            attendees = data.get("attendees", [])
            if isinstance(attendees, list):
                for att in attendees:
                    if isinstance(att, dict):
                        name = att.get("name", "")
                        if name:
                            self.stakeholders.record(name, "meeting", wf_id, ts)

        # Detect decisions from activity patterns (gated on PM mode)
        if not self.pm_mode_active:
            return
        decision = self.decisions.process_event(event)
        if decision:
            if wf_id is not None:
                decision.workflow_id = wf_id
            # Link decision to active workflow
            if wf:
                wf.add_inferred_decision(decision.description, decision.confidence, ts)

    def process_raw_event(self, event: dict[str, Any]) -> None:
        """Process a raw event for input state and monitor updates."""
        event_type = event.get("event_type")
        data = event.get("data")

        if event_type == "input_state" and isinstance(data, dict):
            state = data.get("state", "idle")
            app = data.get("app")  # which app is receiving input (from daemon)
            self.now.update_input_state(state, app)

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
                    duration_seconds=s.get("duration_seconds", 0),
                    projects=s.get("projects", []),
                    files_count=len(s.get("files_edited", [])),
                    tickets=s.get("tickets", []),
                    tools=s.get("tools", []),
                    context_switches=s.get("context_switches", 0),
                    focus_blocks_count=s.get("focus_blocks_count", 0),
                ))
            if saved_sessions:
                logger.info("Loaded %d recent sessions from DB", len(saved_sessions))
        except Exception:
            logger.debug("No saved sessions found (table may not exist yet)")

        # Load workflows
        self.workflows.load_from_db(conn)

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

    async def refresh_workstreams(self) -> bool:
        """Trigger LLM-based workstream refresh if engine is available."""
        if self.workstream_engine is None:
            return False
        try:
            return await self.workstream_engine.maybe_refresh(self.workstreams)
        except Exception:
            logger.debug("Workstream refresh failed", exc_info=True)
            return False

    def to_dict(self) -> dict:
        return {
            "now": self.now.to_dict(),
            "session": self.session.to_dict(),
            "recent_sessions": [s.to_dict() for s in self.recent_sessions[-5:]],
            "projects": self.projects.to_dict(),
            "agents": self.agents.to_dict(),
            "workflows": [wf.to_dict() for wf in self.workflows.list_workflows()],
            "stakeholders": self.stakeholders.to_dict(),
            "artifacts": self.artifacts.to_dict(),
            "decisions": self.decisions.to_dict(),
            "workstreams": self.workstreams.to_dict(),
        }
