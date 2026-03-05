"""LLM-powered session summaries — natural language summary of a work session.

Falls back to a simple template-based summary if LLM is unavailable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context.session import CompactSession, SessionState
    from .provider import LLMProvider

logger = logging.getLogger("lurk.llm")

SYSTEM_PROMPT = """\
You are a work session summarizer. Given structured data about a developer's \
completed work session, write a concise 2-3 sentence summary suitable for a \
daily standup or work log.

Rules:
- Be factual and concise.
- Focus on what was accomplished and what was worked on.
- Mention key projects, files, and tickets.
- Note focus blocks and research if significant.
- Use past tense.
- Do NOT include greetings or meta-commentary."""


def summarize_session(
    provider: LLMProvider | None,
    session: SessionState,
) -> str:
    """Generate a natural language session summary.

    Returns a template-based summary if LLM is unavailable.
    """
    # Always build template fallback
    fallback = _template_summary(session)

    if provider is None:
        return fallback

    try:
        context = _build_session_data(session)
        if not context:
            return fallback

        user_prompt = (
            "Summarize this completed work session in 2-3 sentences:\n\n"
            f"{context}"
        )

        response = provider.generate(user_prompt, system=SYSTEM_PROMPT, max_tokens=150)

        if response and response.text:
            logger.debug("LLM session summary generated (%d tokens)", response.tokens_used)
            return response.text
        return fallback

    except Exception:
        logger.debug("LLM session summary failed, using template")
        return fallback


def summarize_compact_session(
    provider: LLMProvider | None,
    session: CompactSession,
) -> str:
    """Generate a summary for a CompactSession (cross-session memory)."""
    fallback = _compact_template(session)

    if provider is None:
        return fallback

    try:
        lines = []
        duration_min = int((session.end_time - session.start_time) / 60)
        lines.append(f"Duration: {duration_min} minutes")
        if session.projects:
            lines.append(f"Projects: {', '.join(session.projects)}")
        if session.summary:
            lines.append(f"Previous summary: {session.summary}")

        user_prompt = (
            "Summarize this past work session in 1-2 sentences:\n\n"
            + "\n".join(lines)
        )

        response = provider.generate(user_prompt, system=SYSTEM_PROMPT, max_tokens=100)

        if response and response.text:
            return response.text
        return fallback

    except Exception:
        return fallback


def _build_session_data(session: SessionState) -> str:
    """Build structured session data for the LLM."""
    lines = []

    import time
    duration_min = int((time.time() - session.start_time) / 60)
    lines.append(f"Duration: {duration_min} minutes")

    if session.projects_touched:
        lines.append(f"Projects: {', '.join(session.projects_touched[:5])}")
    if session.files_edited:
        lines.append(f"Files edited: {', '.join(session.files_edited[-10:])}")
    if session.tickets_worked:
        lines.append(f"Tickets: {', '.join(session.tickets_worked)}")
    if session.research_trail:
        topics = [r.topic for r in session.research_trail if r.topic]
        if topics:
            lines.append(f"Research: {', '.join(topics[-5:])}")
    if session.focus_blocks:
        blocks = []
        for fb in session.focus_blocks[-3:]:
            dur = int(fb.duration_seconds / 60)
            blocks.append(f"{fb.project or 'unknown'} ({dur} min)")
        lines.append(f"Focus blocks: {', '.join(blocks)}")
    lines.append(f"Context switches: {session.context_switches}")
    if session.tools_used:
        lines.append(f"Tools: {', '.join(session.tools_used[:5])}")

    return "\n".join(lines)


def _template_summary(session: SessionState) -> str:
    """Simple template-based session summary."""
    import time
    duration_min = int((time.time() - session.start_time) / 60)
    parts = []

    if session.projects_touched:
        projects = ", ".join(session.projects_touched[:3])
        parts.append(f"Worked on {projects} for {duration_min} minutes.")
    else:
        parts.append(f"Session lasted {duration_min} minutes.")

    if session.files_edited:
        n = len(session.files_edited)
        parts.append(f"Edited {n} file{'s' if n != 1 else ''}.")

    if session.focus_blocks:
        n = len(session.focus_blocks)
        total_focus = sum(int(fb.duration_seconds / 60) for fb in session.focus_blocks)
        parts.append(f"{n} focus block{'s' if n != 1 else ''} ({total_focus} min total).")

    if session.research_trail:
        topics = [r.topic for r in session.research_trail if r.topic]
        if topics:
            parts.append(f"Researched: {', '.join(topics[-3:])}.")

    return " ".join(parts)


def _compact_template(session: CompactSession) -> str:
    """Template summary for a CompactSession."""
    duration_min = int((session.end_time - session.start_time) / 60)
    if session.projects:
        projects = ", ".join(session.projects[:3])
        return f"Worked on {projects} for {duration_min} minutes."
    return f"Session lasted {duration_min} minutes."
