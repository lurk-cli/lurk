"""Format adapter for CLAUDE.md (Claude Code)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...context.model import ContextModel


def render(model: ContextModel) -> str:
    """Render context for CLAUDE.md format."""
    now = model.now
    session = model.session
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [f"## Current Work Context (updated {ts})", ""]

    # Core context
    if now.file and now.project:
        lines.append(f"Working on: {now.file} in the {now.project} project")
    elif now.file:
        lines.append(f"Working on: {now.file}")
    elif now.project:
        lines.append(f"Working on: the {now.project} project")

    if now.activity and now.activity != "unknown":
        activity_desc = now.activity.replace("_", " ").title()
        if now.sub_activity:
            activity_desc += f" ({now.sub_activity.replace('_', ' ')})"
        lines.append(f"Activity: {activity_desc}")

    if now.language:
        lines.append(f"Language: {now.language}")

    # Duration
    if now.duration_seconds > 120:
        minutes = int(now.duration_seconds / 60)
        lines.append(f"Duration: {minutes} minutes in current focus block")

    # Research trail
    if session.research_trail:
        topics = [r.topic for r in session.research_trail[-5:] if r.topic]
        if topics:
            lines.append(f"Research: {', '.join(topics)}")

    # Recent files
    other_files = [f for f in session.files_edited if f != now.file]
    if other_files:
        lines.append(f"Recent files: {', '.join(other_files[-5:])}")

    # Tools
    if len(now.tools_active) > 1:
        primary = now.app
        others = [t for t in now.tools_active if t != primary]
        if others:
            lines.append(f"Tools active: {primary} (primary), {', '.join(others[-3:])}")

    # Tickets
    if now.ticket:
        lines.append(f"Related ticket: {now.ticket}")
    elif session.tickets_worked:
        lines.append(f"Tickets: {', '.join(session.tickets_worked[-3:])}")

    lines.append("")
    return "\n".join(lines)
