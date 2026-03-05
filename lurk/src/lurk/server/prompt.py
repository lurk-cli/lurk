"""Rules-based prompt generator — creates context preambles without an LLM.

This is the fallback when no LLM provider is configured. It can't synthesize
or infer goals, but it can structure observations clearly and let the consuming
agent connect the dots.

The key principle: lead with what's most useful to the consuming agent.
What the user is working on > what they've been doing > what tools are active.
"""

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
    """Generate a natural language context preamble.

    Builds a concise briefing from observations. Prioritizes specifics
    (document names, topics, projects) over generic labels (activity types,
    app names). Stops when approaching max_tokens.
    """
    if prompt_config is not None:
        max_tokens = prompt_config.max_tokens

    max_chars = max_tokens * 4
    parts: list[str] = []

    now = model.now
    session = model.session

    # 1. What they're working on — the most specific description we can give
    core = _describe_current_work(now)
    if core:
        parts.append(core)

    # 2. How long / how focused
    duration = _describe_focus(now, session)
    if duration and _fits(parts, duration, max_chars):
        parts.append(duration)

    # 3. Activity trail — connects current work to recent context
    narrative = session.narrative()
    if narrative and _fits(parts, narrative, max_chars):
        parts.append(f"Earlier: {narrative}.")

    # 4. Agent work — what coding agents have been building
    agent_ctx = _describe_agent_work()
    if agent_ctx and _fits(parts, agent_ctx, max_chars):
        parts.append(agent_ctx)

    # 5. Research trail
    research = _describe_research(session, prompt_config)
    if research and _fits(parts, research, max_chars):
        parts.append(research)

    # 6. Reference material (secondary monitors, open tabs)
    ref = _describe_references(now, prompt_config)
    if ref and _fits(parts, ref, max_chars):
        parts.append(ref)

    # 7. Related tickets
    ticket = _describe_tickets(now, session)
    if ticket and _fits(parts, ticket, max_chars):
        parts.append(ticket)

    # 8. Active agents
    agents = _describe_agents(model)
    if agents and _fits(parts, agents, max_chars):
        parts.append(agents)

    return " ".join(parts)


def _fits(existing: list[str], new: str, max_chars: int) -> bool:
    current = sum(len(s) for s in existing)
    return current + len(new) + 1 < max_chars


# ---------------------------------------------------------------------------
# Builders — each produces a natural sentence or None
# ---------------------------------------------------------------------------

def _describe_current_work(now) -> str:
    """Describe what the user is working on, leading with specifics."""
    primary = now.get_primary_activity() if hasattr(now, "get_primary_activity") else None

    if primary and primary.input_seconds > 5:
        return _describe_activity_record(primary)

    # Fallback to snapshot fields
    doc = getattr(now, "document_name", None)
    sub = getattr(now, "sub_activity", None) or ""
    topic = getattr(now, "topic", None)
    app = now.app or "an application"

    if doc:
        if sub == "spreadsheet":
            return f"The user is working on spreadsheet \"{doc}\" in {app}."
        elif sub == "presentation":
            return f"The user is working on presentation \"{doc}\" in {app}."
        elif sub == "email_composing":
            return "The user is composing an email."
        elif sub == "email_reading" and topic:
            return f"The user is reading an email about \"{topic}\"."
        elif sub == "email_triage":
            return "The user is going through their email."
        elif sub == "calendar":
            return f"The user is checking their calendar."
        else:
            return f"The user is working on \"{doc}\" in {app}."

    if now.file and now.project:
        activity = now.activity or "coding"
        lang = f" ({now.language})" if now.language else ""
        return f"The user is {activity} on {now.file} in the {now.project} project{lang}."

    if now.project:
        return f"The user is working on the {now.project} project in {app}."

    if topic and sub == "email_reading":
        return f"The user is reading an email about \"{topic}\"."

    if now.activity and now.activity not in ("unknown", "idle"):
        return f"The user is {now.activity} in {app}."

    return ""


def _describe_activity_record(rec) -> str:
    """Describe an ActivityRecord — what the user is actively doing."""
    app = rec.app
    doc = rec.document_name
    sub = rec.sub_activity or ""

    if doc:
        if sub == "spreadsheet":
            return f"The user is actively working on spreadsheet \"{doc}\" in {app}."
        elif sub == "presentation":
            return f"The user is actively working on presentation \"{doc}\" in {app}."
        else:
            return f"The user is actively working on \"{doc}\" in {app}."

    if rec.file and rec.project:
        return f"The user is actively editing {rec.file} in the {rec.project} project ({app})."

    if "ai_chat" in rec.activity or "interacting" in rec.activity:
        return f"The user is actively typing a prompt in {app}."

    return f"The user is actively {rec.activity} in {app}."


def _describe_focus(now, session) -> str | None:
    """How long and how focused."""
    if now.duration_seconds < 120:
        return None

    minutes = int(now.duration_seconds / 60)

    if session.focus_blocks:
        last = session.focus_blocks[-1]
        block_min = int(last.duration_seconds / 60)
        if block_min > 5:
            return f"They've been focused on this for {block_min} minutes."

    return f"They've been at this for {minutes} minutes."


def _describe_research(session, config) -> str | None:
    """What they've been researching."""
    trail = session.research_trail
    if not trail:
        return None

    staleness = 30
    if config is not None:
        staleness = config.research_staleness_minutes

    recent = trail[-1]
    ts = getattr(recent, "ts", None)
    if ts is not None and (time.time() - ts) / 60 > staleness:
        return None

    topics = [r.topic for r in trail[-3:] if r.topic]
    if not topics:
        return None

    if len(topics) == 1:
        return f"They recently looked up {topics[0]}."
    return f"They've been researching {', '.join(topics[:-1])} and {topics[-1]}."


def _describe_references(now, config) -> str | None:
    """What's open for reference alongside the main work."""
    refs = now.get_reference_activities() if hasattr(now, "get_reference_activities") else []
    if refs:
        labels = [r.label() for r in refs[:3]]
        if len(labels) == 1:
            return f"They also have {labels[0]} open for reference."
        return f"Also referencing: {', '.join(labels)}."

    if len(now.monitors) < 2:
        return None

    ref_apps: list[str] = []
    if config is not None:
        ref_apps = [a.lower() for a in config.monitor_reference_apps]

    for m in now.monitors:
        if m.monitor_id != now.active_monitor and m.app and m.title:
            if ref_apps and m.app.lower() not in ref_apps:
                continue
            return f"They have {m.app} showing \"{m.title}\" on another screen."

    return None


def _describe_tickets(now, session) -> str | None:
    """Related tickets."""
    if now.ticket:
        return f"Related ticket: {now.ticket}."
    if getattr(now, "project", None) and session.tickets_worked:
        return f"Working on: {', '.join(session.tickets_worked[-3:])}."
    return None


def _describe_agents(model) -> str | None:
    """Active AI agents."""
    agents = model.agents
    if not agents.sessions:
        return None

    names = {
        "claude_code": "Claude Code", "cursor_agent": "Cursor Agent",
        "codex": "Codex", "chatgpt": "ChatGPT", "copilot": "Copilot",
        "aider": "Aider", "goose": "Goose",
    }

    parts = []
    for s in agents.sessions.values():
        name = names.get(s.tool, s.tool)
        mins = round(s.duration_seconds / 60)
        proj = f" on {s.project}" if s.project else ""
        parts.append(f"{name}{proj} ({mins} min)")

    if not parts:
        return None
    return "Active agents: " + "; ".join(parts) + "."


def _describe_agent_work() -> str | None:
    """Context from recent AI agent sessions."""
    try:
        from ..observers.session_watcher import SessionWatcher
        watcher = SessionWatcher()
        watcher.check_all()
        session = watcher.get_active_session()
    except Exception:
        return None

    if not session:
        return None

    summary = session.summary_text()
    if not summary:
        return None

    return summary[:600]
