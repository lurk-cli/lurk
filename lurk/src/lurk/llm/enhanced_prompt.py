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
You are generating context for an AI agent's system prompt. Your output will be \
injected so the agent understands what the user is working on without being told.

You will receive raw text captured from the user's screen (via OCR). This is \
exactly what they're looking at right now. Synthesize it into 2-4 sentences:

1. GOAL — What is the user trying to accomplish?
2. STATE — Where are they in the task? What's stuck?
3. GROUNDING — Specific names, functions, files, error messages.
4. CONNECTIONS — How do multiple screens relate?

Write natural prose. Be specific. Do NOT mention screenshots or OCR."""


def generate_enhanced_prompt(
    model: ContextModel,
    provider: LLMProvider | None,
    max_tokens: int = 250,
    tool: str = "coding",
    prompt_config: PromptConfig | None = None,
) -> str:
    """Generate a context prompt for a consuming agent.

    Default path (no LLM, no cost): raw screen text + metadata.
    Optional path (LLM configured): synthesized summary.
    Fallback: rules-based prompt from categorical labels.
    """
    # Try raw screen text first — this is the best signal and costs nothing
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

    parts.append(screen_text)

    # Supplementary: metadata not visible in OCR
    now = model.now
    meta = []
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

    if meta:
        parts.append("\n".join(meta))

    return "\n\n".join(parts)


def _get_raw_screen_text() -> str | None:
    """Get formatted raw screen content from the buffer."""
    try:
        from ..observers.screenshot_observer import get_screen_buffer
        buf = get_screen_buffer()
        text = buf.format_for_llm(max_chars=3000)
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
            f"Synthesize this into a {max_tokens}-token context preamble for a "
            f"{tool} AI tool:\n\n{screen_prompt}"
        )
        response = provider.generate(user_prompt, system=SYSTEM_PROMPT, max_tokens=max_tokens)
        if response and response.text:
            logger.debug("LLM synthesis: %d tokens", response.tokens_used)
            return response.text
    except Exception:
        logger.debug("LLM synthesis failed, serving raw screen text")
    return None
