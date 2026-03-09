"""Workstream inference engine — LLM-powered workstream discovery.

Uses local Ollama to periodically analyze buffered user activity signals
and infer coherent workstreams. The engine reads from WorkstreamManager's
staging buffer, builds a structured prompt, calls the LLM, parses the
response, and feeds results back via apply_llm_results().

Gracefully degrades when Ollama is unavailable — no crashes, no blocking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context.workstreams import WorkstreamManager

from .provider import LLMProvider, OLLAMA_URL, DEFAULT_MODEL

log = logging.getLogger("lurk.workstream_engine")

# Default refresh interval — 3 minutes between LLM calls
REFRESH_INTERVAL = 180

# LLM call timeout in seconds
LLM_TIMEOUT = 15

# Max tokens for LLM response
MAX_RESPONSE_TOKENS = 1500

# Max signals to include in prompt (keep prompt bounded)
MAX_EVENTS_IN_PROMPT = 40
MAX_CONVERSATIONS_IN_PROMPT = 10
MAX_DOCUMENTS_IN_PROMPT = 10
MAX_GIT_IN_PROMPT = 10


class WorkstreamEngine:
    """Uses local LLM to infer workstreams from user activity signals."""

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._provider = llm_provider
        self._model = model
        self._last_refresh_ts: float = 0.0

    def should_refresh(self) -> bool:
        """Check if enough time has passed for a refresh."""
        return time.time() - self._last_refresh_ts >= REFRESH_INTERVAL

    async def maybe_refresh(self, manager: WorkstreamManager) -> bool:
        """Called periodically. Only refreshes if interval has passed and buffer is non-empty."""
        if not self.should_refresh():
            return False
        buffer = manager.get_staging_buffer()
        if not buffer:
            return False
        return await self.refresh_workstreams(manager)

    async def refresh_workstreams(self, manager: WorkstreamManager) -> bool:
        """Main entry point: analyze staging buffer and update workstreams.

        Returns True if workstreams were updated.
        """
        buffer = manager.get_staging_buffer()
        if not buffer:
            log.debug("Empty staging buffer, skipping refresh")
            return False

        # Format signals
        signals_text = self._format_signals(buffer)
        if not signals_text.strip():
            log.debug("No meaningful signals after formatting, skipping")
            return False

        # Format existing workstreams for context
        existing_text = self._format_existing_workstreams(
            manager.get_active_workstreams()
        )

        # Build prompt
        prompt = self._build_discovery_prompt(signals_text, existing_text)

        # Call LLM
        response = await self._call_llm(prompt)
        if not response:
            log.debug("No LLM response, skipping workstream refresh")
            return False

        # Parse response
        results = self._parse_llm_response(response)
        if not results:
            log.debug("Could not parse workstream results from LLM response")
            return False

        # Apply results
        try:
            manager.apply_llm_results(results)
            manager.clear_staging_buffer()
            self._last_refresh_ts = time.time()
            log.info(
                "Refreshed workstreams: %d results applied from %d signals",
                len(results),
                len(buffer),
            )
            return True
        except Exception:
            log.warning("Failed to apply LLM results", exc_info=True)
            return False

    # ------------------------------------------------------------------ #
    # Signal formatting
    # ------------------------------------------------------------------ #

    def _format_signals(self, signals: list[dict]) -> str:
        """Format staging buffer signals into readable prompt sections."""
        events: list[str] = []
        conversations: list[str] = []
        documents: list[str] = []
        git_diffs: list[str] = []

        seen_events: set[str] = set()

        for signal in signals:
            sig_type = signal.get("type", "")
            data = signal.get("data", {})
            ts = signal.get("ts", 0)

            if sig_type == "event":
                line = self._format_event(data, ts)
                if line and line not in seen_events:
                    seen_events.add(line)
                    events.append(line)
            elif sig_type == "conversation":
                line = self._format_conversation(data)
                if line:
                    conversations.append(line)
            elif sig_type == "document":
                line = self._format_document(data)
                if line:
                    documents.append(line)
            elif sig_type == "git":
                line = self._format_git(data)
                if line:
                    git_diffs.append(line)

        parts: list[str] = []

        if events:
            trimmed = events[-MAX_EVENTS_IN_PROMPT:]
            parts.append("EVENTS:\n" + "\n".join(trimmed))
        else:
            parts.append("EVENTS:\n(none)")

        if conversations:
            trimmed = conversations[-MAX_CONVERSATIONS_IN_PROMPT:]
            parts.append("CONVERSATIONS:\n" + "\n".join(trimmed))
        else:
            parts.append("CONVERSATIONS:\n(none)")

        if documents:
            trimmed = documents[-MAX_DOCUMENTS_IN_PROMPT:]
            parts.append("DOCUMENTS:\n" + "\n".join(trimmed))
        else:
            parts.append("DOCUMENTS:\n(none)")

        if git_diffs:
            trimmed = git_diffs[-MAX_GIT_IN_PROMPT:]
            parts.append("CODE CHANGES:\n" + "\n".join(trimmed))
        else:
            parts.append("CODE CHANGES:\n(none)")

        return "\n\n".join(parts)

    def _format_event(self, data: dict, ts: float) -> str:
        """Format a single event signal."""
        activity = data.get("activity", "")
        app = data.get("app", "")
        title = data.get("title", "")
        if not app and not title:
            return ""
        time_str = _ts_to_time(ts)
        activity_str = activity if activity else "using"
        title_str = title[:80] if title else "(untitled)"
        return f"{time_str} - {activity_str} in {app}: {title_str}"

    def _format_conversation(self, data: dict) -> str:
        """Format a conversation extract signal."""
        app = data.get("app", "unknown")
        channel = data.get("channel_or_contact") or data.get("channel", "")
        speakers = data.get("speakers", [])
        if isinstance(speakers, list):
            speakers = ", ".join(speakers[:5])
        topic = data.get("topic_summary") or data.get("topic", "")
        decisions = data.get("decisions", [])
        if isinstance(decisions, list):
            decisions = "; ".join(decisions[:3])
        dates = data.get("dates_mentioned") or data.get("key_dates", [])
        if isinstance(dates, list):
            dates = ", ".join(str(d) for d in dates[:3])

        parts = [f"Chat in {app}"]
        if channel:
            parts[0] += f" ({channel})"
        parts[0] += ":"
        if speakers:
            parts.append(f"{speakers} discussing")
        if topic:
            parts.append(topic)
        if decisions:
            parts.append(f"Decisions: {decisions}")
        if dates:
            parts.append(f"Key dates: {dates}")
        return " ".join(parts)

    def _format_document(self, data: dict) -> str:
        """Format a document extract signal."""
        doc_name = data.get("name", "") or data.get("document_name", "")
        doc_type = data.get("type", "") or data.get("doc_type", "")
        app = data.get("app", "unknown")
        headings = data.get("headings", [])
        if isinstance(headings, list):
            headings = ", ".join(
                h.get("text", str(h)) if isinstance(h, dict) else str(h)
                for h in headings[:5]
            )
        content = data.get("content", "") or data.get("summary", "")
        if content and len(content) > 100:
            content = content[:100] + "..."

        parts = [f"Editing {doc_name or 'unnamed'}"]
        if doc_type:
            parts[0] += f" ({doc_type})"
        parts[0] += f" in {app}."
        if headings:
            parts.append(f"Sections: {headings}.")
        if content:
            parts.append(f"Key content: {content}")
        return " ".join(parts)

    def _format_git(self, data: dict) -> str:
        """Format a git diff signal."""
        project = data.get("project", "unknown")
        branch = data.get("branch", "")
        summary = data.get("summary", "")
        if not summary:
            return ""
        branch_str = f" ({branch})" if branch else ""
        return f"Code changes in {project}{branch_str}: {summary[:200]}"

    # ------------------------------------------------------------------ #
    # Existing workstreams formatting
    # ------------------------------------------------------------------ #

    def _format_existing_workstreams(self, workstreams: list) -> str:
        """Format current workstreams for the LLM to reference."""
        if not workstreams:
            return "(none)"

        lines: list[str] = []
        for ws in workstreams[:10]:
            ws_id = getattr(ws, "id", "?")
            goal = getattr(ws, "inferred_goal", "")
            state = getattr(ws, "current_state", "")
            persona = getattr(ws, "persona", "general")
            score = getattr(ws, "activity_score", 0)

            line = f"- ID={ws_id}: {goal}"
            if state:
                line += f" | State: {state}"
            line += f" | Persona: {persona} | Score: {score:.2f}"
            lines.append(line)

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Prompt construction
    # ------------------------------------------------------------------ #

    def _build_discovery_prompt(self, signals_text: str, existing_text: str) -> str:
        """Build the workstream discovery prompt."""
        return f"""\
You analyze a user's recent computer activity to identify what they are working on.
A "workstream" is a coherent thread of work — something the user is trying to accomplish.

Users may be developers, PMs, designers, marketers, or any knowledge worker.
Their work may involve code, documents, messaging, browsing, or any combination.

Here are the user's recent activities:

{signals_text}

EXISTING WORKSTREAMS (update or keep these if still relevant):
{existing_text}

Identify the distinct workstreams. For each, return a JSON object with:
- "maps_to": ID of an existing workstream this matches, or "new"
- "goal": What is the user trying to accomplish? (1 clear sentence)
- "persona": "developer" | "pm" | "designer" | "marketer" | "general"
- "state": What's the current state of this work? (1 sentence)
- "key_people": [list of people names involved]
- "key_decisions": [list of decisions made, if any]
- "confidence": 0.0-1.0 how confident you are
- "artifacts": [list of files/docs/resources being worked on]
- "research": [{{"topic": "...", "source": "..."}}] if they researched something
- "communications": [{{"who": "...", "channel": "...", "summary": "..."}}] if they communicated

Return a JSON array of workstream objects. Only JSON, no other text."""

    # ------------------------------------------------------------------ #
    # LLM calling
    # ------------------------------------------------------------------ #

    async def _call_llm(self, prompt: str) -> str | None:
        """Call the LLM provider (Ollama). Returns response text or None."""
        # Ensure we have a provider
        provider = self._provider
        if provider is None:
            provider = self._try_create_provider()
            if provider is None:
                log.debug("No LLM provider available, skipping workstream inference")
                return None
            self._provider = provider

        # Run the synchronous provider.generate in a thread to avoid blocking
        try:
            response = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: provider.generate(
                        prompt,
                        system="You are an activity analyst. Return only valid JSON arrays.",
                        max_tokens=MAX_RESPONSE_TOKENS,
                    ),
                ),
                timeout=LLM_TIMEOUT,
            )
            if response and response.text:
                log.debug(
                    "LLM response: %d chars, %d tokens",
                    len(response.text),
                    response.tokens_used,
                )
                return response.text
            return None
        except asyncio.TimeoutError:
            log.warning("LLM call timed out after %ds", LLM_TIMEOUT)
            return None
        except Exception:
            log.warning("LLM call failed", exc_info=True)
            # Reset provider cache so next call re-checks availability
            self._provider = None
            return None

    def _try_create_provider(self) -> LLMProvider | None:
        """Try to create an Ollama provider. Returns None if unavailable."""
        try:
            provider = LLMProvider(model=self._model)
            if provider.is_available():
                return provider
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    # Response parsing
    # ------------------------------------------------------------------ #

    def _parse_llm_response(self, response: str) -> list[dict]:
        """Parse LLM JSON response into workstream update dicts.

        Handles common LLM response issues:
        - Markdown code fences around JSON
        - Trailing commas
        - Extra text before/after JSON
        - Individual objects instead of arrays
        """
        if not response or not response.strip():
            return []

        text = response.strip()

        # Strip markdown code fences
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

        # Try direct parse first
        parsed = self._try_parse_json(text)
        if parsed is not None:
            return self._validate_results(parsed)

        # Try to extract JSON array from the text
        array_match = re.search(r"\[[\s\S]*\]", text)
        if array_match:
            parsed = self._try_parse_json(array_match.group())
            if parsed is not None:
                return self._validate_results(parsed)

        # Try to extract individual JSON objects
        objects: list[dict] = []
        for obj_match in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text):
            parsed_obj = self._try_parse_json(obj_match.group())
            if isinstance(parsed_obj, dict):
                objects.append(parsed_obj)
        if objects:
            return self._validate_results(objects)

        log.debug("Could not extract JSON from LLM response: %.200s", text)
        return []

    def _try_parse_json(self, text: str) -> Any:
        """Try to parse JSON, handling common LLM quirks."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try fixing trailing commas (common LLM issue)
        cleaned = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        return None

    def _validate_results(self, parsed: Any) -> list[dict]:
        """Validate and normalize parsed results into a list of dicts."""
        if isinstance(parsed, dict):
            parsed = [parsed]

        if not isinstance(parsed, list):
            return []

        results: list[dict] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            # Must have at least a goal
            if not item.get("goal"):
                continue
            # Normalize fields
            result = {
                "maps_to": item.get("maps_to", "new"),
                "goal": str(item["goal"]),
                "persona": item.get("persona", "general"),
                "state": str(item.get("state", "")),
                "key_people": _ensure_str_list(item.get("key_people", [])),
                "key_decisions": _ensure_str_list(item.get("key_decisions", [])),
                "confidence": _clamp_float(item.get("confidence", 0.5), 0.0, 1.0),
                "artifacts": _ensure_str_list(item.get("artifacts", [])),
                "research": _ensure_dict_list(item.get("research", [])),
                "communications": _ensure_dict_list(item.get("communications", [])),
            }
            results.append(result)

        return results


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _ts_to_time(ts: float) -> str:
    """Convert timestamp to HH:MM format."""
    if not ts or ts <= 0:
        return "??:??"
    try:
        return datetime.fromtimestamp(ts).strftime("%H:%M")
    except (ValueError, OSError):
        return "??:??"


def _ensure_str_list(val: Any) -> list[str]:
    """Ensure a value is a list of strings."""
    if not isinstance(val, list):
        return []
    return [str(v) for v in val if v]


def _ensure_dict_list(val: Any) -> list[dict]:
    """Ensure a value is a list of dicts."""
    if not isinstance(val, list):
        return []
    result: list[dict] = []
    for v in val:
        if isinstance(v, dict):
            result.append(v)
        elif isinstance(v, str):
            result.append({"topic": v})
    return result


def _clamp_float(val: Any, lo: float, hi: float) -> float:
    """Clamp a value to [lo, hi], defaulting to midpoint on error."""
    try:
        f = float(val)
        return max(lo, min(hi, f))
    except (TypeError, ValueError):
        return (lo + hi) / 2
