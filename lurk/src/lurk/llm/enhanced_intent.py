"""LLM-enhanced intent classification — for ambiguous cases.

Only called when rules-based classification is uncertain.
Falls back to rules-based result if LLM fails.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .provider import LLMProvider

logger = logging.getLogger("lurk.llm")

SYSTEM_PROMPT = """\
You are an intent classifier for developer activity. Given information about \
a developer's current app, window title, and recent events, classify their \
intent into exactly one of these categories:

- feature_development: Building new functionality
- debugging: Finding and fixing bugs
- code_review: Reviewing code changes (PRs, diffs)
- testing: Writing or running tests
- refactoring: Restructuring existing code
- documentation: Writing or reading docs
- research: Investigating solutions, reading reference material
- devops: CI/CD, deployment, infrastructure
- communication: Messaging, email
- meeting: Video call, meeting
- context_switching: Rapidly moving between unrelated tasks
- planning: Issue tracking, task management
- unknown: Cannot determine

Reply with ONLY the intent label, nothing else."""


def classify_intent_llm(
    provider: LLMProvider | None,
    app: str,
    title: str,
    activity: str,
    recent_events: list[dict] | None = None,
    rules_result: str = "unknown",
) -> str:
    """Classify intent using LLM, falling back to rules_result.

    Only use for ambiguous cases where rules-based returned 'unknown' or low confidence.
    """
    if provider is None:
        return rules_result

    try:
        context_lines = [
            f"App: {app}",
            f"Title: {title}",
            f"Activity: {activity}",
        ]

        if recent_events:
            context_lines.append("\nRecent events (last 5):")
            for evt in recent_events[-5:]:
                evt_app = evt.get("app", "")
                evt_title = evt.get("title", "")
                context_lines.append(f"  - {evt_app}: {evt_title}")

        user_prompt = "Classify the developer's current intent:\n\n" + "\n".join(context_lines)

        response = provider.generate(user_prompt, system=SYSTEM_PROMPT, max_tokens=20)

        if response and response.text:
            intent = response.text.strip().lower().replace(" ", "_")
            # Validate against known intents
            valid = {
                "feature_development", "debugging", "code_review", "testing",
                "refactoring", "documentation", "research", "devops",
                "communication", "meeting", "context_switching", "planning", "unknown",
            }
            if intent in valid:
                logger.debug("LLM intent: %s (was: %s)", intent, rules_result)
                return intent

        return rules_result

    except Exception:
        logger.debug("LLM intent classification failed, using rules result")
        return rules_result
