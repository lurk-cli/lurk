"""Session watcher — reads AI agent conversation logs to capture what actually happened.

The real context isn't "VSCode is open" or even "3 files changed" — it's the
conversation itself: what was asked, what was tried, what errors were hit,
what code was written, what decisions were made.

Claude Code stores full conversation transcripts as JSONL in ~/.claude/projects/.
This observer reads those files and extracts the meaningful signal:
- What the user asked the agent to do
- What files the agent read, edited, and created
- What commands were run and their results
- The actual code that was written (from Edit/Write tool calls)
- Errors and retries

This feeds into the context model so when you switch tools, the next AI
already knows: "The user just spent 20 minutes with Claude Code building
a git watcher. They created observers/git_watcher.py, modified the HTTP
server to add /changes endpoints, and hit an issue with the diff parser."
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("lurk.observers.session")

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"

# Caps
MAX_USER_MSG_CHARS = 500
MAX_CODE_PREVIEW_CHARS = 1000
MAX_TOOL_OUTPUT_CHARS = 500
MAX_EDITS_TRACKED = 30
MAX_SESSION_SUMMARY_CHARS = 4000


@dataclass
class FileEdit:
    """A single file edit made by an agent."""
    file_path: str
    new_code: str        # the code that was written (new_string from Edit, or full content from Write)
    edit_size: int       # chars changed
    timestamp: str = ""
    tool: str = "Edit"   # Edit or Write

    def short_path(self) -> str:
        parts = self.file_path.split("/")
        if len(parts) > 2:
            return "/".join(parts[-2:])
        return self.file_path


@dataclass
class CommandRun:
    """A command run by the agent."""
    command: str
    output_preview: str = ""
    success: bool = True
    timestamp: str = ""


@dataclass
class AgentSession:
    """Extracted context from an AI agent's conversation session."""
    session_id: str
    project: str
    project_path: str
    branch: str = ""
    started_at: str = ""
    last_active: str = ""
    model: str = ""

    # What the user asked
    user_messages: list[str] = field(default_factory=list)

    # What the agent did
    files_read: list[str] = field(default_factory=list)
    files_edited: list[FileEdit] = field(default_factory=list)
    files_created: list[FileEdit] = field(default_factory=list)
    commands_run: list[CommandRun] = field(default_factory=list)

    # Counters
    total_edits: int = 0
    total_reads: int = 0
    total_tool_calls: int = 0

    def summary_text(self) -> str:
        """Describe what the user has been doing in this agent session.

        Should read naturally — like how you'd tell a colleague what someone
        was just working on. No rigid format, no labels, no bullet points.
        """
        # The best signal for what they're doing is what they asked the agent
        # — their messages describe intent in their own words
        if self.user_messages:
            # Use the most recent substantive message as the core description
            best_msg = ""
            for msg in reversed(self.user_messages):
                if len(msg) > 15:
                    best_msg = msg
                    break
            if best_msg:
                # Trim to a reasonable length, cut at sentence boundary if possible
                preview = best_msg[:300]
                if len(best_msg) > 300:
                    # Try to cut at a sentence
                    for end in (". ", "! ", "? "):
                        idx = preview.rfind(end)
                        if idx > 100:
                            preview = preview[:idx + 1]
                            break
                    else:
                        preview += "..."

                context = f"User was working with Claude Code on {self.project}"
                if self.branch and self.branch not in ("main", "master", "HEAD"):
                    context += f" ({self.branch})"
                return f"{context}: \"{preview}\""

        # Fallback if no user messages — describe from edits
        if self.files_edited:
            areas = list(dict.fromkeys(_human_area(e.file_path) for e in self.files_edited))
            return f"User was working on {self.project}, editing {', '.join(areas[:5])}."

        return f"User had an active session on {self.project}."

    def to_dict(self) -> dict:
        edited_paths = list(dict.fromkeys(e.short_path() for e in self.files_edited))
        return {
            "session_id": self.session_id,
            "project": self.project,
            "project_path": self.project_path,
            "branch": self.branch,
            "started_at": self.started_at,
            "last_active": self.last_active,
            "model": self.model,
            "files_created": [e.short_path() for e in self.files_created],
            "files_edited": edited_paths,
            "total_edits": self.total_edits,
            "total_reads": self.total_reads,
            "total_tool_calls": self.total_tool_calls,
            "summary": self.summary_text(),
        }


def _human_area(file_path: str) -> str:
    """Convert a file path to a human-readable area name."""
    p = Path(file_path)
    name = p.stem.replace("_", " ")
    parent = p.parent.name
    if parent and parent not in ("src", "lib", ".", ""):
        return name
    return name


def _extract_docstring(code: str) -> str:
    """Extract the first docstring from a code snippet."""
    for marker in ('"""', "'''"):
        start = code.find(marker)
        if start == -1:
            continue
        end = code.find(marker, start + 3)
        if end == -1:
            continue
        doc = code[start + 3:end].strip()
        # Take first sentence only
        if ". " in doc:
            doc = doc[:doc.index(". ") + 1]
        return doc[:200]
    return ""


def _extract_definitions(code: str) -> list[str]:
    """Extract class and function names from a code snippet."""
    names: list[str] = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("class ") and "(" in stripped:
            name = stripped[6:stripped.index("(")].strip()
            if name and name not in names:
                names.append(name)
        elif stripped.startswith("def ") and "(" in stripped:
            name = stripped[4:stripped.index("(")].strip()
            if name and not name.startswith("_") and name not in names:
                names.append(name)
        elif stripped.startswith("async def ") and "(" in stripped:
            name = stripped[10:stripped.index("(")].strip()
            if name and not name.startswith("_") and name not in names:
                names.append(name)
    return names


class SessionWatcher:
    """Watches AI agent session files for conversation context.

    Currently supports:
    - Claude Code (~/.claude/projects/*/session.jsonl)

    Future:
    - Cursor chat history
    - Codex session logs
    """

    def __init__(self) -> None:
        self._watched_sessions: dict[str, float] = {}  # session_path -> last_size
        self._sessions: dict[str, AgentSession] = {}     # session_id -> parsed session
        self._last_scan: float = 0
        self.scan_interval: float = 15  # seconds between scans

    def check_all(self) -> list[AgentSession]:
        """Check for new/updated agent sessions. Returns sessions with new data."""
        now = time.time()
        if now - self._last_scan < self.scan_interval:
            return []
        self._last_scan = now

        updated = []

        # Scan Claude Code sessions
        if PROJECTS_DIR.exists():
            for project_dir in PROJECTS_DIR.iterdir():
                if not project_dir.is_dir():
                    continue
                for jsonl_file in project_dir.glob("*.jsonl"):
                    session = self._check_session_file(jsonl_file, project_dir.name)
                    if session:
                        updated.append(session)

        return updated

    def get_session(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def get_recent_sessions(self, limit: int = 5) -> list[AgentSession]:
        """Get most recently active sessions."""
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.last_active,
            reverse=True,
        )
        return sessions[:limit]

    def get_active_session(self) -> AgentSession | None:
        """Get the most recently active session (within last 10 min)."""
        sessions = self.get_recent_sessions(limit=1)
        if not sessions:
            return None
        s = sessions[0]
        # Check file modification time — most reliable freshness signal
        for path, _ in self._watched_sessions.items():
            if s.session_id in path:
                try:
                    mtime = os.path.getmtime(path)
                    if time.time() - mtime < 600:
                        return s
                except Exception:
                    pass
        return None

    def build_session_context(self, session_id: str | None = None) -> str:
        """Build context from an agent session for use in prompts/workflows."""
        if session_id:
            session = self.get_session(session_id)
        else:
            session = self.get_active_session()

        if not session:
            return ""

        return session.summary_text()

    def _check_session_file(self, path: Path, project_dir_name: str) -> AgentSession | None:
        """Check if a session JSONL file has new data. Returns parsed session if updated."""
        path_str = str(path)
        current_size = path.stat().st_size

        last_size = self._watched_sessions.get(path_str, 0)
        if current_size == last_size:
            return None  # No change

        self._watched_sessions[path_str] = current_size

        # Parse the session
        session_id = path.stem
        session = self._parse_claude_session(path, session_id, project_dir_name)
        if session:
            self._sessions[session_id] = session
            return session
        return None

    def _parse_claude_session(self, path: Path, session_id: str, project_dir_name: str) -> AgentSession | None:
        """Parse a Claude Code JSONL session file."""
        # Decode project path from directory name (e.g. "-Users-jasonzhao-Documents-lurk")
        project_path = project_dir_name.replace("-", "/")
        if project_path.startswith("/"):
            pass  # already absolute-ish
        else:
            project_path = "/" + project_path
        project_name = project_path.rsplit("/", 1)[-1]

        session = AgentSession(
            session_id=session_id,
            project=project_name,
            project_path=project_path,
        )

        try:
            with open(path, "r", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._process_entry(session, entry)
        except Exception:
            logger.debug("Error parsing session %s", path, exc_info=True)
            return None

        if not session.user_messages and not session.files_edited:
            return None

        return session

    def _process_entry(self, session: AgentSession, entry: dict) -> None:
        """Process a single JSONL entry from a Claude Code session."""
        entry_type = entry.get("type", "")

        # Extract session metadata
        if entry.get("gitBranch") and not session.branch:
            session.branch = entry["gitBranch"]
        if entry.get("version") and not session.model:
            session.model = entry.get("model", entry.get("version", ""))
        if entry.get("timestamp"):
            ts = entry["timestamp"]
            if isinstance(ts, str):
                session.last_active = ts
            elif isinstance(ts, (int, float)):
                if ts > 1e12:  # milliseconds
                    ts = ts / 1000
                session.last_active = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
        if not session.started_at and session.last_active:
            session.started_at = session.last_active

        # User messages — what they asked the agent to do
        if entry_type == "user":
            msg = entry.get("message", {})
            content = msg.get("content", "")
            text = self._extract_text(content)
            if text and len(text) > 5:
                # Skip tool results (they're just confirmations)
                if not entry.get("toolUseResult"):
                    session.user_messages.append(text[:MAX_USER_MSG_CHARS])

        # Assistant messages — tool calls are the interesting part
        elif entry_type == "assistant":
            msg = entry.get("message", {})
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        self._process_tool_use(session, block, entry.get("timestamp", ""))

    def _process_tool_use(self, session: AgentSession, block: dict, timestamp: Any) -> None:
        """Process a tool_use block to extract what the agent did."""
        tool_name = block.get("name", "")
        tool_input = block.get("input", {})
        session.total_tool_calls += 1

        ts_str = ""
        if isinstance(timestamp, str):
            ts_str = timestamp
        elif isinstance(timestamp, (int, float)):
            if timestamp > 1e12:
                timestamp = timestamp / 1000
            ts_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp))

        if tool_name == "Read":
            file_path = tool_input.get("file_path", "")
            if file_path and file_path not in session.files_read:
                session.files_read.append(file_path)
                session.total_reads += 1

        elif tool_name == "Edit":
            file_path = tool_input.get("file_path", "")
            new_string = tool_input.get("new_string", "")
            old_string = tool_input.get("old_string", "")
            if file_path:
                session.total_edits += 1
                if len(session.files_edited) < MAX_EDITS_TRACKED:
                    session.files_edited.append(FileEdit(
                        file_path=file_path,
                        new_code=new_string,
                        edit_size=len(new_string) - len(old_string),
                        timestamp=ts_str,
                        tool="Edit",
                    ))

        elif tool_name == "Write":
            file_path = tool_input.get("file_path", "")
            content = tool_input.get("content", "")
            if file_path:
                session.total_edits += 1
                fe = FileEdit(
                    file_path=file_path,
                    new_code=content[:MAX_CODE_PREVIEW_CHARS],
                    edit_size=len(content),
                    timestamp=ts_str,
                    tool="Write",
                )
                session.files_created.append(fe)
                if len(session.files_edited) < MAX_EDITS_TRACKED:
                    session.files_edited.append(fe)

        elif tool_name == "Bash":
            command = tool_input.get("command", "")
            if command:
                session.commands_run.append(CommandRun(
                    command=command,
                    timestamp=ts_str,
                ))

    def _extract_text(self, content: Any) -> str:
        """Extract text content from a message content field."""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        # Skip tool results — they're responses, not user intent
                        pass
                elif isinstance(block, str):
                    texts.append(block)
            return " ".join(texts).strip()
        return ""
