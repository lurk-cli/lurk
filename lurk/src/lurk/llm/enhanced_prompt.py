"""LLM-enhanced prompt generation — richer, more natural context preambles.

Falls back to rules-based prompt.py if LLM is unavailable.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..server.prompt import generate_prompt as rules_based_prompt

if TYPE_CHECKING:
    from ..config.settings import PromptConfig
    from ..context.model import ContextModel
    from .provider import LLMProvider

logger = logging.getLogger("lurk.llm")

SYSTEM_PROMPT = """\
You are a context summarizer for an AI coding assistant. Given structured data \
about what a developer is currently doing, write a concise natural language \
preamble (2-4 sentences). This preamble will be injected into the system prompt \
of AI tools so they understand the user's current context without being told.

Rules:
- Be concise and factual. No speculation.
- Start with what the user is doing right now.
- Include duration, research trail, recent files, and tickets if provided.
- Do NOT include greetings, opinions, or meta-commentary.
- Do NOT mention lurk or the context system — just describe what the user is doing.
- Use present tense. Write as if briefing a colleague.
- Stay within the token budget."""


def generate_enhanced_prompt(
    model: ContextModel,
    provider: LLMProvider | None,
    max_tokens: int = 250,
    tool: str = "coding",
    prompt_config: PromptConfig | None = None,
) -> str:
    """Generate a context preamble, using LLM if available.

    Always returns a valid prompt — falls back to rules-based if LLM fails.
    """
    # Always generate rules-based as fallback
    fallback = rules_based_prompt(model, max_tokens, tool, prompt_config=prompt_config)

    if provider is None:
        return fallback

    try:
        context_data = _build_context_data(model)
        if not context_data:
            return fallback

        user_prompt = (
            f"Generate a concise context preamble ({max_tokens} tokens max) "
            f"for a {tool} AI tool based on this developer's current state:\n\n"
            f"{context_data}"
        )

        response = provider.generate(user_prompt, system=SYSTEM_PROMPT, max_tokens=max_tokens)

        if response and response.text:
            logger.debug("LLM-enhanced prompt generated (%d tokens)", response.tokens_used)
            return response.text
        else:
            return fallback

    except Exception:
        logger.debug("LLM prompt generation failed, using rules-based fallback")
        return fallback


def _build_context_data(model: ContextModel) -> str:
    """Build structured context data string for the LLM."""
    now = model.now
    session = model.session
    lines = []

    if now.app:
        lines.append(f"App: {now.app}")
    if now.file:
        lines.append(f"File: {now.file}")
    if now.project:
        lines.append(f"Project: {now.project}")
    if now.language:
        lines.append(f"Language: {now.language}")
    if now.activity and now.activity != "unknown":
        lines.append(f"Activity: {now.activity}")
    if now.sub_activity:
        lines.append(f"Sub-activity: {now.sub_activity}")
    if now.intent:
        lines.append(f"Intent: {now.intent}")
    if now.duration_seconds > 60:
        lines.append(f"Duration: {int(now.duration_seconds / 60)} minutes")
    if now.input_state != "idle":
        lines.append(f"Input state: {now.input_state}")
    if now.ticket:
        lines.append(f"Ticket: {now.ticket}")

    # Session context
    if session.files_edited:
        recent = session.files_edited[-5:]
        lines.append(f"Recent files: {', '.join(recent)}")
    if session.research_trail:
        topics = [r.topic for r in session.research_trail[-3:] if r.topic]
        if topics:
            lines.append(f"Research topics: {', '.join(topics)}")
    if session.tickets_worked:
        lines.append(f"Tickets: {', '.join(session.tickets_worked[-3:])}")
    if session.focus_blocks:
        last = session.focus_blocks[-1]
        lines.append(f"Focus block: {int(last.duration_seconds / 60)} min on {last.project or 'current project'}")
    if session.context_switches > 0:
        lines.append(f"Context switches: {session.context_switches}")

    # Monitor context
    if len(now.monitors) > 1:
        for m in now.monitors:
            if m.monitor_id != now.active_monitor and m.app:
                lines.append(f"Secondary monitor: {m.app} — {m.title or 'unknown'}")
                break

    # Cross-session
    if model.recent_sessions:
        last = model.recent_sessions[-1]
        if last.projects:
            lines.append(f"Previous session projects: {', '.join(last.projects[:3])}")

    return "\n".join(lines)
