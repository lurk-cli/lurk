"""Context prompt generation — raw screen content for consuming agents.

The consuming agent (Claude Code, Cursor, ChatGPT) IS an LLM. It doesn't
need us to pre-digest the screen content through another LLM call. That's
an extra cost, an extra latency hit, and a lossy transformation.

Instead: capture the screen via OCR, format it with minimal metadata, and
hand it directly to the consuming agent. Let IT read the code, the errors,
the browser content, and figure out what the user is doing.

If an LLM provider IS configured (Ollama, API key), it can optionally
synthesize a shorter summary. But the default path — which costs nothing
and requires no configuration — is raw screen text + metadata.
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

# System prompt only used when an LLM provider is explicitly configured.
# Most users won't hit this path.
SYSTEM_PROMPT = """\
You are generating a brief context summary from raw screen text captured via OCR.

Rules:
- ONLY describe what is directly visible on screen. Do NOT infer goals or intent.
- Use specific names: file names, function names, error messages, URLs, people names.
- Do NOT interpret code variable names as the user's intent or project goals.
- Do NOT mention screenshots, OCR, or how the data was captured.
- If you see code, describe what file is open and what function/section is visible.
- If you see a chat app, describe who the conversation is with and the topic.
- If you see a document, describe the document title and what section is visible.
- Keep it to 2-4 sentences. Be factual, not speculative."""


def generate_enhanced_prompt(
    model: ContextModel,
    provider: LLMProvider | None,
    max_tokens: int = 250,
    tool: str = "coding",
    prompt_config: PromptConfig | None = None,
) -> str:
    """Generate a context prompt for a consuming agent.

    Priority:
    1. Workstream-based cold start (if workstreams exist with inferred goals)
    2. Raw screen text + metadata (no LLM, no cost)
    3. Optional LLM synthesis of screen text
    4. Rules-based prompt from categorical labels
    """
    # Try workstream-based cold start first
    try:
        from .synthesis import format_cold_start_xml, format_cold_start_human

        primary = (
            model.workstreams.get_primary_workstream()
            if hasattr(model, "workstreams") and hasattr(model.workstreams, "get_primary_workstream")
            else None
        )
        if primary and primary.inferred_goal:
            active = model.workstreams.get_active_workstreams()
            secondary = [ws for ws in active if ws.id != primary.id][:2]
            if tool == "coding":
                return format_cold_start_xml(primary, model, secondary)
            else:
                return format_cold_start_human(primary, model, secondary)
    except Exception:
        logger.debug("Workstream synthesis unavailable, falling back")

    # Try raw screen text — this is the best signal and costs nothing
    screen_prompt = _build_screen_prompt(model)
    if screen_prompt:
        # If an LLM is configured, optionally synthesize a shorter version
        if provider is not None:
            synthesized = _try_llm_synthesis(provider, screen_prompt, max_tokens, tool)
            if synthesized:
                return synthesized
        # Otherwise return the screen text directly — the consuming agent IS an LLM
        return screen_prompt

    # No screen text available — fall back to rules-based prompt
    return rules_based_prompt(model, max_tokens, tool, prompt_config=prompt_config)


def _build_screen_prompt(model: ContextModel) -> str | None:
    """Build a context prompt from raw screen captures + metadata.

    No LLM call. No API key. No cost. The raw screen text IS the context.
    The consuming agent is an LLM — let it interpret what's on screen.
    """
    parts = []

    # Primary signal: what's on screen right now
    screen_text = _get_raw_screen_text()
    if not screen_text:
        return None

    parts.append("## What's on screen")
    parts.append(screen_text)

    # Note if user is typing in a different app than the primary screen
    now = model.now
    input_app = getattr(now, "input_app", None)
    if input_app and now.app and input_app.lower() != now.app.lower():
        parts.append(f"User is typing in {input_app} (not the primary screen).")

    # Brief note if leisure content detected on secondary screens (already filtered from OCR)
    try:
        from ..observers.screenshot_observer import get_screen_buffer
        buf = get_screen_buffer()
        leisure_apps = set()
        for f in buf.frames[-5:]:
            if getattr(f, 'relevance', 'work') == 'leisure':
                leisure_apps.add(f.app)
        if leisure_apps:
            parts.append(f"(Ignoring background entertainment: {', '.join(sorted(leisure_apps))})")
    except Exception:
        pass

    # Supplementary: metadata not visible in OCR
    meta = ["## Additional context"]
    if now.project and now.branch:
        meta.append(f"Project: {now.project} (branch: {now.branch})")
    elif now.project:
        meta.append(f"Project: {now.project}")
    if now.duration_seconds > 120:
        meta.append(f"Active for {int(now.duration_seconds / 60)} minutes")

    # Activity trail — what they did before the current screen
    session = model.session
    narrative = session.narrative()
    if narrative:
        meta.append(f"Earlier: {narrative}")

    # Workflow decisions — what's been decided so far
    wf = model.workflows.get_active_workflow() if hasattr(model, 'workflows') else None
    if wf and wf.key_decisions:
        meta.append("Decisions: " + "; ".join(wf.key_decisions[-3:]))
    if wf and wf.agent_contributions:
        for agent_tool, summary in wf.agent_contributions.items():
            meta.append(f"{agent_tool}: {summary}")

    # PM context: stakeholders, artifacts, decisions
    if hasattr(model, 'stakeholders'):
        recent_stakeholders = model.stakeholders.get_recent(5)
        if recent_stakeholders:
            names = [s.name for s in recent_stakeholders]
            meta.append(f"Recent contacts: {', '.join(names)}")

    if hasattr(model, 'artifacts'):
        recent_artifacts = model.artifacts.get_recent(3)
        if recent_artifacts:
            artifact_parts = [f"'{a.name}' ({a.status.value})" for a in recent_artifacts]
            meta.append(f"Documents: {', '.join(artifact_parts)}")

    if hasattr(model, 'decisions'):
        recent_decisions = model.decisions.get_recent(hours=2, limit=3)
        if recent_decisions:
            dec_parts = [d.description for d in recent_decisions if d.confidence >= 0.6]
            if dec_parts:
                meta.append(f"Recent decisions: {'; '.join(dec_parts)}")

    if len(meta) > 1:  # more than just the header
        parts.append("\n".join(meta))

    return "\n\n".join(parts)


def _get_raw_screen_text() -> str | None:
    """Get formatted raw screen content from the buffer."""
    try:
        from ..observers.screenshot_observer import get_screen_buffer
        buf = get_screen_buffer()
        text = buf.format_for_llm(max_chars=4500)
        return text if text else None
    except Exception:
        return None


def _try_llm_synthesis(
    provider: LLMProvider,
    screen_prompt: str,
    max_tokens: int,
    tool: str,
) -> str | None:
    """Optionally synthesize a shorter prompt via LLM.

    Only called when a provider is explicitly configured. Most users
    skip this entirely and serve raw screen text directly.
    """
    try:
        user_prompt = (
            f"Describe what is visible on screen in {max_tokens} tokens or less. "
            f"Only state facts, do not speculate about intent:\n\n{screen_prompt}"
        )
        response = provider.generate(user_prompt, system=SYSTEM_PROMPT, max_tokens=max_tokens)
        if response and response.text:
            logger.debug("LLM synthesis: %d tokens", response.tokens_used)
            return response.text
    except Exception:
        logger.debug("LLM synthesis failed, serving raw screen text")
    return None
