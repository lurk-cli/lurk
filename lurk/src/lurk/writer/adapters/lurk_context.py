"""Format adapter for .lurk-context.md (generic/universal format)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...context.model import ContextModel


def render(model: ContextModel) -> str:
    """Render context in the universal .lurk-context.md format."""
    now = model.now
    session = model.session
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# lurk Context — {ts}",
        "",
        "## Current State",
        "",
    ]

    if now.app:
        lines.append(f"- **App**: {now.app}")
    if now.file:
        lines.append(f"- **File**: {now.file}")
    if now.project:
        lines.append(f"- **Project**: {now.project}")
    if now.language:
        lines.append(f"- **Language**: {now.language}")
    if now.activity and now.activity != "unknown":
        lines.append(f"- **Activity**: {now.activity}")
    if now.intent:
        lines.append(f"- **Intent**: {now.intent.replace('_', ' ')}")
    if now.ticket:
        lines.append(f"- **Ticket**: {now.ticket}")
    if now.duration_seconds > 120:
        lines.append(f"- **Duration**: {int(now.duration_seconds / 60)} min")
    if now.input_state != "idle":
        lines.append(f"- **Input**: {now.input_state}")

    # Session info
    if session.files_edited or session.research_trail:
        lines.extend(["", "## Session", ""])

        if session.files_edited:
            lines.append(f"- **Files edited**: {', '.join(session.files_edited[-10:])}")
        if session.tickets_worked:
            lines.append(f"- **Tickets**: {', '.join(session.tickets_worked)}")
        if session.research_trail:
            topics = [r.topic for r in session.research_trail[-5:] if r.topic]
            if topics:
                lines.append(f"- **Research**: {', '.join(topics)}")
        lines.append(f"- **Context switches**: {session.context_switches}")
        if session.focus_blocks:
            lines.append(f"- **Focus blocks**: {len(session.focus_blocks)}")

    # Monitor context
    if len(now.monitors) > 1:
        lines.extend(["", "## Monitors", ""])
        seen = set()
        for m in now.monitors:
            if m.app and m.app not in seen:
                marker = " (active)" if m.monitor_id == now.active_monitor else ""
                title_part = f" — {m.title}" if m.title else ""
                lines.append(f"- Monitor {m.monitor_id}: {m.app}{title_part}{marker}")
                seen.add(m.app)

    lines.append("")
    return "\n".join(lines)
