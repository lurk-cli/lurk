"""Agent detector — identifies AI coding agents from window titles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Terminal apps that host CLI-based agents
TERMINAL_APPS = {"terminal", "iterm2", "iterm", "alacritty", "kitty", "warp", "hyper", "wezterm"}

# Browser apps that host web-based agents
BROWSER_APPS = {"google chrome", "safari", "arc", "brave browser", "firefox", "microsoft edge", "chrome"}


@dataclass
class AgentDetection:
    """Result of agent detection from a window title."""
    agent_tool: str  # e.g. "claude_code", "cursor_agent", "aider"
    agent_state: str  # e.g. "working", "blocked", "needs_review", "completed", "errored", "idle"


def detect_agent(app: str, title: str, bundle_id: str | None = None) -> AgentDetection | None:
    """
    Detect an AI agent from app name + window title.

    Returns AgentDetection if an agent is detected, None otherwise.
    """
    if not title:
        return None

    app_lower = app.lower() if app else ""
    title_lower = title.lower()

    # Claude Code — runs in terminal apps
    if _is_terminal(app_lower, bundle_id) and "claude" in title_lower:
        return AgentDetection(
            agent_tool="claude_code",
            agent_state=_classify_claude_code_state(title),
        )

    # Cursor Agent — runs in Cursor IDE
    if "cursor" in app_lower:
        state = _classify_cursor_state(title)
        if state:
            return AgentDetection(agent_tool="cursor_agent", agent_state=state)

    # Aider — runs in terminal apps
    if _is_terminal(app_lower, bundle_id) and "aider" in title_lower:
        return AgentDetection(
            agent_tool="aider",
            agent_state=_classify_aider_state(title),
        )

    # Goose — runs in terminal apps
    if _is_terminal(app_lower, bundle_id) and "goose" in title_lower:
        return AgentDetection(agent_tool="goose", agent_state="working")

    # OpenClaw / MyClaw — runs in browser
    if _is_browser(app_lower, bundle_id) and ("openclaw" in title_lower or "myclaw" in title_lower):
        return AgentDetection(
            agent_tool="openclaw",
            agent_state=_classify_openclaw_state(title),
        )

    # Codex — runs in terminal apps
    if _is_terminal(app_lower, bundle_id) and "codex" in title_lower:
        return AgentDetection(
            agent_tool="codex",
            agent_state=_classify_codex_state(title),
        )

    # ChatGPT — runs in browser
    if _is_browser(app_lower, bundle_id) and "chatgpt" in title_lower:
        return AgentDetection(agent_tool="chatgpt", agent_state="working")

    # GitHub Copilot — runs in browser or editor
    if _is_browser(app_lower, bundle_id) and "copilot" in title_lower:
        if "workspace" in title_lower:
            return AgentDetection(agent_tool="copilot_workspace", agent_state="working")
        return AgentDetection(agent_tool="copilot", agent_state="working")

    return None


def _is_terminal(app_lower: str, bundle_id: str | None) -> bool:
    """Check if app is a terminal emulator."""
    if any(t in app_lower for t in TERMINAL_APPS):
        return True
    if bundle_id:
        bid = bundle_id.lower()
        if "terminal" in bid or "iterm" in bid or "warp" in bid:
            return True
    return False


def _is_browser(app_lower: str, bundle_id: str | None) -> bool:
    """Check if app is a web browser."""
    if any(b in app_lower for b in BROWSER_APPS):
        return True
    if bundle_id:
        bid = bundle_id.lower()
        if "chrome" in bid or "safari" in bid or "firefox" in bid or "arc" in bid:
            return True
    return False


def _classify_claude_code_state(title: str) -> str:
    """Classify Claude Code state from terminal title."""
    t = title.lower()

    # Error state
    if "error:" in t or "error " in t:
        return "errored"

    # Blocked / needs input
    if any(s in t for s in ["[y/n]", "? ", "allow", "permission", "approve"]):
        return "blocked"

    # Completed
    if any(s in t for s in ["done", "$ ", "completed"]):
        return "completed"

    # Working indicators
    if any(s in t for s in ["thinking", "reading", "writing", "searching", "running", "editing"]):
        return "working"

    # Default — if we see "claude" it's likely active
    return "working"


def _classify_cursor_state(title: str) -> str | None:
    """Classify Cursor agent state. Returns None if no agent activity detected."""
    t = title.lower()

    # Agent/composing indicators
    if any(s in t for s in ["composing", "agent", "generating"]):
        return "working"

    # Review indicators
    if any(s in t for s in ["review", "accept", "diff", "changes"]):
        return "needs_review"

    # No agent activity detected in Cursor
    return None


def _classify_aider_state(title: str) -> str:
    """Classify Aider state from terminal title."""
    t = title.lower()

    if "error:" in t or "error " in t:
        return "errored"

    if any(s in t for s in ["thinking", "editing", "applying"]):
        return "working"

    return "working"


def _classify_codex_state(title: str) -> str:
    """Classify Codex state from terminal title."""
    t = title.lower()

    if "error:" in t or "error " in t:
        return "errored"

    if any(s in t for s in ["[y/n]", "approve", "allow", "confirm"]):
        return "blocked"

    if any(s in t for s in ["thinking", "reading", "writing", "running", "editing"]):
        return "working"

    if any(s in t for s in ["done", "completed", "$ "]):
        return "completed"

    return "working"


def _classify_openclaw_state(title: str) -> str:
    """Classify OpenClaw/MyClaw state from browser title."""
    t = title.lower()

    if any(s in t for s in ["running", "executing", "working"]):
        return "working"

    if any(s in t for s in ["review", "waiting", "needs review"]):
        return "needs_review"

    return "working"
