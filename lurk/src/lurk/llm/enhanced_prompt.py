"""LLM-enhanced prompt generation — produces context that makes agents perform better.

Working backwards from what Anthropic recommends for effective agent prompts:
the consuming agent needs to understand the user's GOAL, not their app. It needs
RELEVANT STATE, not an activity log. It needs GROUNDING specifics (names, projects,
artifacts), not abstract descriptions. And it needs MINIMAL, HIGH-SIGNAL tokens —
every word should earn its place.

The LLM synthesizes raw observations into this. The rules-based fallback does
its best without synthesis.
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

# This system prompt is the most important part of lurk's output quality.
# It tells the LLM how to turn raw observations into context that makes
# the consuming agent perform better.
SYSTEM_PROMPT = """\
You are generating context for an AI agent's system prompt. Your output will be \
injected so the agent understands what the user is working on without being told.

Your job: synthesize raw observations into a brief, useful context preamble that \
helps the agent be immediately helpful.

What makes good context for an agent:
1. GOAL — What is the user trying to accomplish? Infer from their activity trail. \
Not "they're in Excel" but "they're analyzing Q3 revenue data."
2. STATE — Where are they in the task? What's done, what's in progress? \
Not "they edited 5 files" but "they've built the data model and are now wiring up the API."
3. GROUNDING — Specific names, projects, documents, topics that let the agent \
give concrete answers. Not "a spreadsheet" but "the Q3 Revenue Forecast spreadsheet."
4. CONNECTIONS — How do recent activities relate? If they read an email about \
Project Alpha then opened a spreadsheet, say so. Don't list disconnected facts.

Rules:
- Write 2-4 sentences of natural prose. No bullet points, no labels, no formatting.
- Synthesize, don't list. Connect the dots between activities.
- Be specific — use actual names, titles, and topics from the data.
- Infer the goal from the activity pattern. Research + spreadsheet + email = preparing something.
- Write in present tense, as if briefing a colleague about what someone is doing right now.
- Do NOT mention the observation system, context system, or any meta-commentary.
- Do NOT speculate beyond what the data supports. If you can't infer the goal, \
describe the current activity with specifics.
- Stay within the token budget. Every word should help the consuming agent."""


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
            f"Synthesize this into a {max_tokens}-token context preamble for a "
            f"{tool} AI tool. Focus on the user's goal and current state, not "
            f"raw observations:\n\n{context_data}"
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
    """Build the observation data that the LLM will synthesize.

    Organized by signal value, not by source. The LLM's job is to connect
    these into a coherent picture of what the user is doing and why.
    """
    now = model.now
    session = model.session
    sections = []

    # --- What they're doing right now ---
    current = []
    if now.app:
        current.append(f"Currently in: {now.app}")
    if now.document_name:
        current.append(f"Document: \"{now.document_name}\"")
    if now.file and now.project:
        current.append(f"Editing: {now.file} in {now.project}")
    elif now.file:
        current.append(f"Editing: {now.file}")
    elif now.project:
        current.append(f"Project: {now.project}")
    if now.activity and now.activity not in ("unknown", "idle"):
        current.append(f"Activity: {now.activity}")
    if now.sub_activity:
        current.append(f"Specifically: {now.sub_activity}")
    if now.language:
        current.append(f"Language: {now.language}")
    if now.ticket:
        current.append(f"Ticket: {now.ticket}")
    if now.branch:
        current.append(f"Branch: {now.branch}")
    if now.duration_seconds > 120:
        current.append(f"Been at this: {int(now.duration_seconds / 60)} minutes")
    if current:
        sections.append("CURRENT:\n" + "\n".join(current))

    # --- What they've been doing (the trail that reveals intent) ---
    narrative = session.narrative()
    if narrative:
        sections.append(f"ACTIVITY TRAIL:\n{narrative}")

    # --- Reference material (what's open alongside) ---
    refs = now.get_reference_activities() if hasattr(now, "get_reference_activities") else []
    if refs:
        ref_lines = [f"- {r.label()} in {r.app}" for r in refs[:3]]
        sections.append("ALSO OPEN FOR REFERENCE:\n" + "\n".join(ref_lines))
    elif len(now.monitors) > 1:
        for m in now.monitors:
            if m.monitor_id != now.active_monitor and m.app and m.title:
                sections.append(f"SECONDARY MONITOR: {m.app} showing \"{m.title}\"")
                break

    # --- Research (what they've been looking up) ---
    if session.research_trail:
        topics = [r.topic for r in session.research_trail[-5:] if r.topic]
        if topics:
            sections.append(f"RECENTLY RESEARCHED: {', '.join(topics)}")

    # --- Agent work product (code changes, session context) ---
    agent_context = _get_agent_context()
    if agent_context:
        sections.append(f"RECENT AGENT WORK:\n{agent_context}")

    # --- Workflow context (accumulated from all observers) ---
    wf = model.workflows.get_active_workflow() if hasattr(model, 'workflows') else None
    if wf:
        wf_parts = []
        if wf.label:
            wf_parts.append(f"Workflow: {wf.label}")
        if wf.agent_contributions:
            for tool, summary in wf.agent_contributions.items():
                wf_parts.append(f"{tool}: {summary}")
        if wf.breadcrumbs:
            recent = wf.breadcrumbs[-6:]
            trail = list(dict.fromkeys(recent))
            wf_parts.append("Trail: " + " → ".join(trail))
        if wf.research:
            topics = [r["topic"] for r in wf.research[-3:]]
            wf_parts.append(f"Researched: {', '.join(topics)}")
        if wf.code_changes:
            wf_parts.append("Code changes: " + "; ".join(wf.code_changes[-5:]))
        if wf.documents:
            doc_items = [f'"{n}"' + (f" ({d})" if d else "") for n, d in list(wf.documents.items())[-3:]]
            wf_parts.append(f"Documents: {', '.join(doc_items)}")
        if wf.key_decisions:
            wf_parts.append("Decisions: " + "; ".join(wf.key_decisions[-3:]))
        if wf.projects:
            wf_parts.append(f"Projects: {', '.join(wf.projects[:3])}")
        if wf_parts:
            sections.append("WORKFLOW CONTEXT:\n" + "\n".join(wf_parts))

    # --- Cross-session continuity ---
    if model.recent_sessions:
        last = model.recent_sessions[-1]
        if last.projects:
            import time
            age_hours = (time.time() - last.end_time) / 3600
            if age_hours < 24:
                sections.append(
                    f"EARLIER TODAY: worked on {', '.join(last.projects[:3])}"
                )

    return "\n\n".join(sections)


def _get_agent_context() -> str | None:
    """Get context from recent AI agent sessions."""
    try:
        from ..observers.session_watcher import SessionWatcher
        watcher = SessionWatcher()
        watcher.check_all()
        session = watcher.get_active_session()
        if session:
            return session.summary_text()
    except Exception:
        pass
    return None
