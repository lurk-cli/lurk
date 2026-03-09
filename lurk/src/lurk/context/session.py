"""Session tracking — detect work sessions, focus blocks, and context switches."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class FocusBlock:
    """A contiguous period of focused work on one project."""
    start: float
    end: float = 0
    project: str = ""
    activity: str = ""
    files_touched: list[str] = field(default_factory=list)
    switch_count: int = 0

    @property
    def duration_seconds(self) -> float:
        return (self.end or time.time()) - self.start

    @property
    def depth_score(self) -> float:
        """Higher = deeper focus. Duration weighted by absence of switches."""
        minutes = self.duration_seconds / 60
        return minutes * (1 / (1 + self.switch_count))

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "project": self.project,
            "activity": self.activity,
            "files_touched": self.files_touched[:20],
            "duration_seconds": round(self.duration_seconds),
            "depth_score": round(self.depth_score, 2),
        }


@dataclass
class ResearchEntry:
    """A single research action."""
    ts: float
    topic: str
    domain: str | None = None
    duration_seconds: float = 0

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "topic": self.topic,
            "domain": self.domain,
            "duration_seconds": round(self.duration_seconds),
        }


@dataclass
class ActivityBreadcrumb:
    """A snapshot of what the user was doing at a point in time."""
    ts: float
    description: str  # e.g. "reading email about project Alpha", "editing Q3 Revenue spreadsheet"
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {"ts": self.ts, "description": self.description, "duration_seconds": round(self.duration_seconds, 1)}


@dataclass
class SessionState:
    """State of the current work session."""
    start_time: float = field(default_factory=time.time)
    projects_touched: list[str] = field(default_factory=list)
    files_edited: list[str] = field(default_factory=list)
    tickets_worked: list[str] = field(default_factory=list)
    research_trail: list[ResearchEntry] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    context_switches: int = 0
    focus_blocks: list[FocusBlock] = field(default_factory=list)
    # Running narrative — breadcrumbs of what the user has been doing
    breadcrumbs: list[ActivityBreadcrumb] = field(default_factory=list)

    # Caps
    MAX_PROJECTS = 20
    MAX_FILES = 100
    MAX_TICKETS = 20
    MAX_RESEARCH = 50
    MAX_TOOLS = 20
    MAX_FOCUS_BLOCKS = 20
    MAX_BREADCRUMBS = 30

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.start_time

    def add_project(self, project: str) -> None:
        if project and project not in self.projects_touched:
            self.projects_touched.append(project)
            if len(self.projects_touched) > self.MAX_PROJECTS:
                self.projects_touched = self.projects_touched[-self.MAX_PROJECTS:]

    def add_file(self, file: str) -> None:
        if file and file not in self.files_edited:
            self.files_edited.append(file)
            if len(self.files_edited) > self.MAX_FILES:
                self.files_edited = self.files_edited[-self.MAX_FILES:]

    def add_ticket(self, ticket: str) -> None:
        if ticket and ticket not in self.tickets_worked:
            self.tickets_worked.append(ticket)
            if len(self.tickets_worked) > self.MAX_TICKETS:
                self.tickets_worked = self.tickets_worked[-self.MAX_TICKETS:]

    def add_research(self, entry: ResearchEntry) -> None:
        self.research_trail.append(entry)
        if len(self.research_trail) > self.MAX_RESEARCH:
            self.research_trail = self.research_trail[-self.MAX_RESEARCH:]

    def add_tool(self, tool: str) -> None:
        if tool and tool not in self.tools_used:
            self.tools_used.append(tool)
            if len(self.tools_used) > self.MAX_TOOLS:
                self.tools_used = self.tools_used[-self.MAX_TOOLS:]

    def add_breadcrumb(self, ts: float, description: str) -> None:
        """Record what the user was doing — dedupes consecutive identical descriptions."""
        if self.breadcrumbs and self.breadcrumbs[-1].description == description:
            return  # Same activity, skip
        # Stamp duration on the previous breadcrumb
        if self.breadcrumbs:
            self.breadcrumbs[-1].duration_seconds = ts - self.breadcrumbs[-1].ts
        self.breadcrumbs.append(ActivityBreadcrumb(ts=ts, description=description))
        if len(self.breadcrumbs) > self.MAX_BREADCRUMBS:
            self.breadcrumbs = self.breadcrumbs[-self.MAX_BREADCRUMBS:]

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format duration nicely: >= 60s as minutes, < 60s as seconds."""
        if seconds >= 60:
            minutes = int(seconds // 60)
            return f"{minutes}m"
        return f"{int(seconds)}s"

    def narrative(self) -> str:
        """Build a dwell-aware narrative of what the user has been doing this session."""
        if not self.breadcrumbs:
            return ""
        recent = self.breadcrumbs[-10:]
        parts: list[str] = []
        quick_group: list[str] = []

        def flush_quick() -> None:
            if quick_group:
                parts.append(f"quick lookups: {', '.join(quick_group)}")
                quick_group.clear()

        for b in recent:
            if b.duration_seconds >= 30:
                flush_quick()
                dur = self._format_duration(b.duration_seconds)
                parts.append(f"{b.description} ({dur})")
            elif b.duration_seconds > 0:
                quick_group.append(b.description)
            else:
                # No duration yet (current/last item) — show without duration
                flush_quick()
                parts.append(b.description)

        flush_quick()
        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for p in parts:
            if p not in seen:
                seen.add(p)
                deduped.append(p)
        return " \u2192 ".join(deduped)

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time,
            "duration_seconds": round(self.duration_seconds),
            "projects_touched": self.projects_touched,
            "files_edited": self.files_edited,
            "tickets_worked": self.tickets_worked,
            "research_trail": [r.to_dict() for r in self.research_trail],
            "tools_used": self.tools_used,
            "context_switches": self.context_switches,
            "focus_blocks": [f.to_dict() for f in self.focus_blocks],
            "narrative": self.narrative(),
        }


@dataclass
class CompactSession:
    """Compact summary of a completed session."""
    start_time: float
    end_time: float
    duration_seconds: float
    projects: list[str]
    files_count: int
    tickets: list[str]
    tools: list[str]
    context_switches: int
    focus_blocks_count: int

    def to_dict(self) -> dict:
        return self.__dict__


def _describe_activity(
    app: str, activity: str, project: str | None, file: str | None,
    topic: str | None, document_name: str | None, sub_activity: str | None,
) -> str:
    """Turn an enriched event into a natural description of what the user is doing."""
    if document_name:
        if sub_activity == "spreadsheet":
            return f"working on spreadsheet \"{document_name}\""
        if sub_activity == "presentation":
            return f"working on presentation \"{document_name}\""
        return f"working on \"{document_name}\""

    if sub_activity == "email_reading" and topic:
        return f"reading email about \"{topic}\""
    if sub_activity == "email_composing":
        return "composing an email"
    if sub_activity == "email_triage":
        return "going through email"

    if activity == "researching" and topic:
        return f"researching \"{topic}\""

    if activity == "coding" and file and project:
        return f"editing {file} in {project}"
    if activity == "coding" and project:
        return f"coding on {project}"

    if sub_activity == "code_review" and topic:
        return f"reviewing \"{topic}\""

    if activity == "communicating" and topic:
        return f"in a conversation about \"{topic}\""

    if activity == "browsing" and topic:
        return f"looking at \"{topic}\""

    if activity not in ("unknown", "idle") and app:
        return f"{activity} in {app}"

    return ""


class SessionTracker:
    """Tracks session boundaries and maintains session state."""

    def __init__(self, idle_threshold: float = 300) -> None:
        self.idle_threshold = idle_threshold  # 5 minutes
        self.current_session = SessionState()
        self.recent_sessions: list[CompactSession] = []
        self.last_event_ts: float = time.time()
        self._last_app: str | None = None
        self._last_project: str | None = None
        self._current_focus_block: FocusBlock | None = None
        self._last_research_start: float = 0
        self._last_research_topic: str | None = None
        self._last_research_domain: str | None = None
        self._last_meeting_title: str | None = None
        self._last_meeting_end_ts: float = 0
        self._post_meeting_window: float = 900  # 15 minutes

    def process_event(self, event: dict) -> None:
        """Process an enriched event and update session state."""
        ts = event.get("ts", time.time())
        app = event.get("app", "")
        activity = event.get("activity", "unknown")
        project = event.get("project")
        file = event.get("file")
        ticket = event.get("ticket")
        topic = event.get("topic")
        domain = event.get("url_domain")

        # Check for idle gap → session boundary
        gap = ts - self.last_event_ts
        if gap > self.idle_threshold and self.last_event_ts > 0:
            self._close_session()
            self.current_session = SessionState(start_time=ts)

        self.last_event_ts = ts

        # Update session
        if project:
            self.current_session.add_project(project)
        if file:
            self.current_session.add_file(file)
        if ticket:
            self.current_session.add_ticket(ticket)
        if app:
            self.current_session.add_tool(app)

        # Track context switches
        if app != self._last_app and self._last_app is not None:
            self.current_session.context_switches += 1

        # Track focus blocks
        self._update_focus_block(ts, app, project, activity, file)

        # Track research trail
        if activity == "researching" and topic:
            if self._last_research_start and self._last_research_topic:
                # Close previous research entry
                duration = ts - self._last_research_start
                self.current_session.add_research(ResearchEntry(
                    ts=self._last_research_start,
                    topic=self._last_research_topic,
                    domain=self._last_research_domain,
                    duration_seconds=duration,
                ))
            # Start new research entry
            self._last_research_start = ts
            self._last_research_topic = topic
            self._last_research_domain = domain
        elif self._last_research_start and activity != "researching":
            # Stopped researching — close the entry
            duration = ts - self._last_research_start
            self.current_session.add_research(ResearchEntry(
                ts=self._last_research_start,
                topic=self._last_research_topic or "",
                domain=self._last_research_domain,
                duration_seconds=duration,
            ))
            self._last_research_start = 0
            self._last_research_topic = None
            self._last_research_domain = None

        # Build breadcrumb — describe what the user is doing in natural language
        crumb = _describe_activity(app, activity, project, file, topic,
                                   event.get("document_name"),
                                   event.get("sub_activity"))
        if crumb:
            self.current_session.add_breadcrumb(ts, crumb)

        # Post-meeting linking — detect follow-up activity after meetings
        if activity == "meeting":
            self._last_meeting_title = topic or event.get("document_name") or "meeting"
            self._last_meeting_end_ts = ts
        elif (
            self._last_meeting_title
            and activity in ("writing", "planning", "communicating", "coding")
            and ts - self._last_meeting_end_ts < self._post_meeting_window
        ):
            follow_up = f"follow-up from meeting '{self._last_meeting_title}'"
            self.current_session.add_breadcrumb(ts, follow_up)
            self._last_meeting_title = None

        self._last_app = app
        self._last_project = project

    def _update_focus_block(
        self, ts: float, app: str, project: str | None,
        activity: str, file: str | None
    ) -> None:
        """Update or create focus blocks."""
        if project and project == self._last_project:
            # Continue current focus block
            if self._current_focus_block:
                self._current_focus_block.end = ts
                if file and file not in self._current_focus_block.files_touched:
                    self._current_focus_block.files_touched.append(file)
            else:
                self._current_focus_block = FocusBlock(
                    start=ts, end=ts, project=project,
                    activity=activity,
                    files_touched=[file] if file else [],
                )
        else:
            # Project changed — close current focus block
            if self._current_focus_block and self._current_focus_block.duration_seconds > 120:
                self.current_session.focus_blocks.append(self._current_focus_block)
                if len(self.current_session.focus_blocks) > SessionState.MAX_FOCUS_BLOCKS:
                    self.current_session.focus_blocks = (
                        self.current_session.focus_blocks[-SessionState.MAX_FOCUS_BLOCKS:]
                    )
            if project:
                self._current_focus_block = FocusBlock(
                    start=ts, end=ts, project=project,
                    activity=activity,
                    files_touched=[file] if file else [],
                )
            else:
                self._current_focus_block = None

    def _close_session(self) -> None:
        """Close the current session and create a compact summary."""
        session = self.current_session
        # Close any open focus block
        if self._current_focus_block:
            if self._current_focus_block.duration_seconds > 120:
                session.focus_blocks.append(self._current_focus_block)
            self._current_focus_block = None

        compact = CompactSession(
            start_time=session.start_time,
            end_time=self.last_event_ts,
            duration_seconds=self.last_event_ts - session.start_time,
            projects=session.projects_touched[:],
            files_count=len(session.files_edited),
            tickets=session.tickets_worked[:],
            tools=session.tools_used[:],
            context_switches=session.context_switches,
            focus_blocks_count=len(session.focus_blocks),
        )
        self.recent_sessions.append(compact)
        # Keep last 50 sessions
        if len(self.recent_sessions) > 50:
            self.recent_sessions = self.recent_sessions[-50:]
