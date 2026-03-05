"""Format adapter for .cursorrules (Cursor)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...context.model import ContextModel


def render(model: ContextModel) -> str:
    """Render context for .cursorrules format."""
    now = model.now
    session = model.session
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [f"# Current Work Context (updated {ts})", ""]

    # Cursor rules format — concise, directive style
    if now.project:
        lines.append(f"- Currently working on the {now.project} project")
    if now.file:
        lines.append(f"- Active file: {now.file}")
    if now.language:
        lines.append(f"- Language: {now.language}")
    if now.intent:
        lines.append(f"- Current intent: {now.intent.replace('_', ' ')}")

    # Recent files for context
    other_files = [f for f in session.files_edited if f != now.file]
    if other_files:
        lines.append(f"- Recently edited: {', '.join(other_files[-5:])}")

    # Research context
    if session.research_trail:
        topics = [r.topic for r in session.research_trail[-3:] if r.topic]
        if topics:
            lines.append(f"- Recent research: {', '.join(topics)}")

    if now.ticket:
        lines.append(f"- Related ticket: {now.ticket}")

    lines.append("")
    return "\n".join(lines)
