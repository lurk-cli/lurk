"""Natural language prompt generator — creates context preambles for AI tools."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..config.settings import PromptConfig
    from ..context.model import ContextModel


def generate_prompt(
    model: ContextModel,
    max_tokens: int = 250,
    tool: str = "coding",
    prompt_config: PromptConfig | None = None,
) -> str:
    """
    Generate a natural language context preamble.

    Priority-ordered sections with relevance gating. Stops when approaching max_tokens.
    1 token ~ 4 characters heuristic.
    """
    if prompt_config is not None:
        max_tokens = prompt_config.max_tokens

    max_chars = max_tokens * 4
    sections: list[str] = []

    now = model.now
    session = model.session

    # Core context and duration are always relevant
    core = _build_core(now)
    if core:
        sections.append(core)

    duration = _build_duration(now, session)
    if duration and _fits(sections, duration, max_chars):
        sections.append(duration)

    # Build gated optional sections
    optional: list[tuple[str, str]] = []

    research = _build_research_gated(session, prompt_config)
    if research:
        optional.append(("research", research))

    monitors = _build_monitors_gated(now, prompt_config)
    if monitors:
        optional.append(("monitors", monitors))

    files = _build_files_gated(session, now.file, prompt_config)
    if files:
        optional.append(("files", files))

    cross_session = _build_cross_session_gated(model, prompt_config)
    if cross_session:
        optional.append(("cross_session", cross_session))

    ticket = _build_ticket_gated(now, session)
    if ticket:
        optional.append(("ticket", ticket))

    agents = _build_agents_gated(model)
    if agents:
        optional.append(("agents", agents))

    # Intent-aware reordering
    optional = _reorder_by_intent(optional, now)

    # Append what fits
    for _label, text in optional:
        if _fits(sections, text, max_chars):
            sections.append(text)

    return " ".join(sections)


def _fits(existing: list[str], new: str, max_chars: int) -> bool:
    """Check if adding new section stays within budget."""
    current = sum(len(s) for s in existing)
    return current + len(new) + 1 < max_chars


def _reorder_by_intent(
    sections: list[tuple[str, str]],
    now,
) -> list[tuple[str, str]]:
    """Reorder optional sections based on current intent/activity."""
    intent = getattr(now, "intent", None) or ""
    activity = getattr(now, "activity", None) or ""

    priority_map: dict[str, list[str]] = {
        "researching": ["research", "monitors", "agents", "cross_session", "files", "ticket"],
        "debugging": ["files", "ticket", "agents", "research", "monitors", "cross_session"],
        "coding": ["files", "ticket", "agents", "research", "monitors", "cross_session"],
        "writing": ["monitors", "research", "agents", "cross_session", "files", "ticket"],
    }

    # Pick priority from intent first, then activity
    key = intent.lower() if intent else activity.lower()
    order = priority_map.get(key)
    if not order:
        return sections

    def sort_key(item: tuple[str, str]) -> int:
        label = item[0]
        try:
            return order.index(label)
        except ValueError:
            return len(order)

    return sorted(sections, key=sort_key)


# ---------------------------------------------------------------------------
# Core builders (always included)
# ---------------------------------------------------------------------------

def _build_core(now) -> str:
    """Build the core context sentence."""
    parts = []

    activity = now.activity or "working"
    app = now.app or "an application"

    if now.file and now.project:
        parts.append(
            f"The user is {activity} in {app}, editing {now.file} "
            f"in the {now.project} project."
        )
    elif now.file:
        parts.append(f"The user is {activity} in {app}, editing {now.file}.")
    elif now.project:
        parts.append(f"The user is {activity} in {app} on the {now.project} project.")
    else:
        parts.append(f"The user is {activity} in {app}.")

    if now.language:
        parts.append(f"Language: {now.language}.")

    return " ".join(parts)


def _build_duration(now, session) -> str | None:
    """Build duration and focus context."""
    if now.duration_seconds < 60:
        return None

    minutes = int(now.duration_seconds / 60)

    # Check focus blocks
    focus_blocks = session.focus_blocks
    if focus_blocks:
        last_block = focus_blocks[-1]
        block_minutes = int(last_block.duration_seconds / 60)
        if block_minutes > 5:
            return f"They've been in a focused session for {block_minutes} minutes."

    if minutes > 2:
        return f"They've been on this for {minutes} minutes."

    return None


# ---------------------------------------------------------------------------
# Gated builders (relevance-filtered)
# ---------------------------------------------------------------------------

def _build_research_gated(session, config: PromptConfig | None) -> str | None:
    """Build research trail — skip if stale."""
    trail = session.research_trail
    if not trail:
        return None

    staleness_minutes = 30
    if config is not None:
        staleness_minutes = config.research_staleness_minutes

    # Check freshness of most recent entry
    recent = trail[-1]
    ts = getattr(recent, "timestamp", None) or getattr(recent, "ts", None)
    if ts is not None:
        age_minutes = (time.time() - ts) / 60
        if age_minutes > staleness_minutes:
            return None

    last_entries = trail[-3:]
    topics = [r.topic for r in last_entries if r.topic]
    if not topics:
        return None

    domains = [r.domain for r in last_entries if r.domain]

    if len(topics) == 1:
        source = f" on {domains[0]}" if domains else ""
        return f"They recently researched {topics[0]}{source}."
    else:
        topic_str = ", ".join(topics[:-1]) + f" and {topics[-1]}"
        return f"They recently researched {topic_str}."


def _build_monitors_gated(now, config: PromptConfig | None) -> str | None:
    """Build multi-monitor context — skip if secondary app isn't reference material."""
    if len(now.monitors) < 2:
        return None

    reference_apps: list[str] = []
    if config is not None:
        reference_apps = [a.lower() for a in config.monitor_reference_apps]

    secondary = [
        m for m in now.monitors
        if m.monitor_id != now.active_monitor and m.app and m.title
    ]
    if not secondary:
        return None

    m = secondary[0]

    # If config provides reference apps, filter
    if reference_apps and m.app.lower() not in reference_apps:
        return None

    return f"They have {m.app} open on their secondary monitor showing \"{m.title}\"."


def _build_files_gated(session, current_file: str | None, config: PromptConfig | None) -> str | None:
    """Build recent files context — skip if fewer than min_files edited."""
    files = [f for f in session.files_edited if f != current_file]
    if not files:
        return None

    min_files = 2
    if config is not None:
        min_files = config.min_files_for_inclusion

    if len(files) < min_files:
        return None

    recent = files[-5:]
    if len(recent) == 1:
        return f"They also recently edited {recent[0]}."
    else:
        return f"Recent files: {', '.join(recent)}."


def _build_cross_session_gated(model: ContextModel, config: PromptConfig | None) -> str | None:
    """Build cross-session context — skip if too old or no project overlap."""
    recent = model.recent_sessions
    if not recent:
        return None

    last = recent[-1]
    if not last.projects:
        return None

    max_hours = 24
    if config is not None:
        max_hours = config.cross_session_max_hours

    age_hours = (time.time() - last.end_time) / 3600
    if age_hours > max_hours:
        return None

    # Check project overlap with current session
    current_project = getattr(model.now, "project", None)
    if current_project and current_project not in last.projects:
        return None

    if age_hours < 24:
        time_str = f"{int(age_hours)} hours ago"
    else:
        days = int(age_hours / 24)
        time_str = f"{days} day{'s' if days > 1 else ''} ago"

    projects_str = ", ".join(last.projects[:3])
    return f"In a previous session ({time_str}), they worked on {projects_str}."


def _build_agents_gated(model: ContextModel) -> str | None:
    """Build agent status summary — skip if no agents active."""
    agents = model.agents
    if not agents.sessions:
        return None

    parts: list[str] = []
    for session in agents.sessions.values():
        name = _agent_display(session.tool)
        duration_min = round(session.duration_seconds / 60)
        state_desc = session.state.replace("_", " ")
        proj = f" on {session.project}" if session.project else ""
        parts.append(f"{name} {state_desc}{proj} ({duration_min} min)")

    if not parts:
        return None

    return "Active agents: " + "; ".join(parts) + "."


def _agent_display(tool: str) -> str:
    """Convert tool ID to display name for prompts."""
    names = {
        "claude_code": "Claude Code",
        "cursor_agent": "Cursor Agent",
        "aider": "Aider",
        "goose": "Goose",
        "openclaw": "OpenClaw",
        "copilot_workspace": "Copilot Workspace",
    }
    return names.get(tool, tool)


def _build_ticket_gated(now, session) -> str | None:
    """Build ticket context — skip if no current project context."""
    if now.ticket:
        return f"Related ticket: {now.ticket}."

    # Only include session tickets if we have project context
    if not getattr(now, "project", None):
        return None

    tickets = session.tickets_worked
    if tickets:
        return f"Tickets worked on: {', '.join(tickets[-3:])}."
    return None
