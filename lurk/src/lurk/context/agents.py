"""Agent registry — tracks concurrent AI agent sessions and attention queue."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentTool(str, Enum):
    CLAUDE_CODE = "claude_code"
    CURSOR_AGENT = "cursor_agent"
    AIDER = "aider"
    GOOSE = "goose"
    OPENCLAW = "openclaw"
    COPILOT_WORKSPACE = "copilot_workspace"
    CHATGPT = "chatgpt"
    CLAUDE_WEB = "claude_web"
    GEMINI = "gemini"
    COPILOT_WEB = "copilot_web"
    PERPLEXITY = "perplexity"


class AgentState(str, Enum):
    WORKING = "working"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    ERRORED = "errored"
    IDLE = "idle"


@dataclass
class AgentSession:
    """A tracked AI agent session, keyed by tool+project."""
    tool: str
    state: str
    project: str | None = None
    task: str | None = None
    started_at: float = 0.0
    last_state_change: float = 0.0
    last_seen: float = 0.0
    files_involved: list[str] = field(default_factory=list)
    human_interventions: int = 0
    total_human_time: float = 0.0
    _human_focus_start: float | None = field(default=None, repr=False)

    @property
    def session_id(self) -> str:
        return f"{self.tool}:{self.project or 'unknown'}"

    @property
    def duration_seconds(self) -> float:
        return time.time() - self.started_at if self.started_at else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tool": self.tool,
            "state": self.state,
            "project": self.project,
            "task": self.task,
            "started_at": self.started_at,
            "duration_seconds": round(self.duration_seconds),
            "last_state_change": self.last_state_change,
            "last_seen": self.last_seen,
            "files_involved": self.files_involved[-10:],
            "human_interventions": self.human_interventions,
            "total_human_time": round(self.total_human_time),
        }


@dataclass
class AttentionItem:
    """An item in the attention queue — an agent needing human attention."""
    session_id: str
    tool: str
    reason: str
    priority: int  # 1=highest (errored), 2=needs_review, 3=blocked, 4=completed
    state: str
    time_waiting: float
    project: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tool": self.tool,
            "reason": self.reason,
            "priority": self.priority,
            "state": self.state,
            "time_waiting": round(self.time_waiting),
            "project": self.project,
        }


# Priority mapping for attention queue
_STATE_PRIORITY = {
    AgentState.ERRORED.value: (1, "has errored"),
    AgentState.NEEDS_REVIEW.value: (2, "needs review"),
    AgentState.BLOCKED.value: (3, "is blocked waiting for input"),
    AgentState.COMPLETED.value: (4, "has completed"),
}

# Stale session timeout (seconds)
DEFAULT_STALE_TIMEOUT = 600.0  # 10 minutes


class AgentRegistry:
    """Tracks concurrent AI agent sessions."""

    def __init__(self, stale_timeout: float = DEFAULT_STALE_TIMEOUT) -> None:
        self.sessions: dict[str, AgentSession] = {}
        self.completed_sessions: list[AgentSession] = []
        self.stale_timeout = stale_timeout

    def process_event(self, event: dict[str, Any]) -> None:
        """Process an enriched event that has agent_tool/agent_state."""
        agent_tool = event.get("agent_tool")
        agent_state = event.get("agent_state")

        if not agent_tool:
            return

        now = time.time()
        ts = event.get("ts", now)
        project = event.get("project")
        file_path = event.get("file")
        session_key = f"{agent_tool}:{project or 'unknown'}"

        # Find or create session
        session = self.sessions.get(session_key)
        if session is None:
            session = AgentSession(
                tool=agent_tool,
                state=agent_state or "working",
                project=project,
                started_at=ts,
                last_state_change=ts,
                last_seen=ts,
            )
            self.sessions[session_key] = session

        # Update state
        old_state = session.state
        new_state = agent_state or old_state
        if new_state != old_state:
            session.state = new_state
            session.last_state_change = ts

        session.last_seen = ts

        # Track files
        if file_path and file_path not in session.files_involved:
            session.files_involved.append(file_path)
            if len(session.files_involved) > 50:
                session.files_involved = session.files_involved[-50:]

        # Track human focus time — if the user is actively looking at this agent's window
        if session._human_focus_start is None:
            session._human_focus_start = ts
            session.human_interventions += 1
        session.total_human_time += max(0, ts - (session._human_focus_start or ts))
        session._human_focus_start = ts

        # Move completed/stale sessions
        self._cleanup(now)

    def _cleanup(self, now: float) -> None:
        """Move completed and stale sessions out of active tracking."""
        to_remove = []
        for key, session in self.sessions.items():
            age = now - session.last_seen
            if age > self.stale_timeout:
                session.state = AgentState.IDLE.value
                self.completed_sessions.append(session)
                to_remove.append(key)
            elif session.state == AgentState.COMPLETED.value and age > 60:
                # Keep completed sessions visible for 60s, then archive
                self.completed_sessions.append(session)
                to_remove.append(key)

        for key in to_remove:
            del self.sessions[key]

        # Keep only last 20 completed sessions
        if len(self.completed_sessions) > 20:
            self.completed_sessions = self.completed_sessions[-20:]

    def get_attention_queue(self) -> list[AttentionItem]:
        """Get priority-sorted list of agents needing attention."""
        now = time.time()
        items: list[AttentionItem] = []

        for session in self.sessions.values():
            if session.state in _STATE_PRIORITY:
                priority, reason = _STATE_PRIORITY[session.state]
                items.append(AttentionItem(
                    session_id=session.session_id,
                    tool=session.tool,
                    reason=f"{_tool_display_name(session.tool)} {reason}",
                    priority=priority,
                    state=session.state,
                    time_waiting=now - session.last_state_change,
                    project=session.project,
                ))

        items.sort(key=lambda x: (x.priority, -x.time_waiting))
        return items

    def get_handoff_context(
        self, from_session_id: str, to_tool: str
    ) -> dict[str, Any]:
        """Generate a handoff briefing from one agent session to another."""
        # Find the source session (active or completed)
        source = self.sessions.get(from_session_id)
        if source is None:
            # Search completed sessions
            for s in reversed(self.completed_sessions):
                if s.session_id == from_session_id:
                    source = s
                    break

        if source is None:
            return {"error": f"Session '{from_session_id}' not found."}

        duration_min = round(source.duration_seconds / 60)
        files_str = ", ".join(source.files_involved[-5:]) if source.files_involved else "none tracked"

        summary = (
            f"{_tool_display_name(source.tool)} was working on "
            f"{source.project or 'unknown project'} for {duration_min} minutes. "
            f"Final state: {source.state}. "
            f"Files involved: {files_str}."
        )

        if source.task:
            summary += f" Task: {source.task}."

        return {
            "from_tool": source.tool,
            "from_state": source.state,
            "to_tool": to_tool,
            "project": source.project,
            "duration_minutes": duration_min,
            "files_involved": source.files_involved[-10:],
            "task": source.task,
            "human_interventions": source.human_interventions,
            "summary": summary,
        }

    def get_workflow_summary(self) -> dict[str, Any]:
        """Get a high-level summary of agent workflow."""
        active = [s for s in self.sessions.values() if s.state != AgentState.IDLE.value]
        active_count = len(active)

        # Determine workflow pattern
        if active_count == 0:
            pattern = "idle"
        elif active_count == 1:
            pattern = "single_agent"
        else:
            projects = {s.project for s in active if s.project}
            if len(projects) > 1:
                pattern = "multi_stream"
            else:
                pattern = "parallel"

        # By-project breakdown
        by_project: dict[str, list[dict]] = {}
        for s in active:
            proj = s.project or "unknown"
            if proj not in by_project:
                by_project[proj] = []
            by_project[proj].append({
                "tool": s.tool,
                "state": s.state,
                "duration_minutes": round(s.duration_seconds / 60),
            })

        attention = self.get_attention_queue()

        return {
            "active_agents": active_count,
            "pattern": pattern,
            "by_project": by_project,
            "attention_needed": len(attention),
            "recent_completed": len(self.completed_sessions),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize active sessions and recent completed."""
        return {
            "active_sessions": {
                key: session.to_dict()
                for key, session in self.sessions.items()
            },
            "completed_sessions": [
                s.to_dict() for s in self.completed_sessions[-5:]
            ],
            "summary": self.get_workflow_summary(),
        }


def _tool_display_name(tool: str) -> str:
    """Convert tool ID to human-readable name."""
    names = {
        "claude_code": "Claude Code",
        "cursor_agent": "Cursor Agent",
        "aider": "Aider",
        "goose": "Goose",
        "openclaw": "OpenClaw",
        "copilot_workspace": "Copilot Workspace",
        "chatgpt": "ChatGPT",
        "claude_web": "Claude (Web)",
        "gemini": "Gemini",
        "copilot_web": "Copilot (Web)",
        "perplexity": "Perplexity",
    }
    return names.get(tool, tool)
