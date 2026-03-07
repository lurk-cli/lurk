"""AI chat observer — tracks web-based AI tool usage for PM context.

Processes browser extension's `extension_input` data (already flowing via
POST /context/enrich). Creates breadcrumbs like "asking ChatGPT: 'help me
structure the Q2 roadmap...'" and links AI chat sessions to workflows.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .base import WorkflowUpdate


# Known AI chat tools and their URL/title patterns
_AI_CHAT_PATTERNS: dict[str, list[str]] = {
    "ChatGPT": ["chat.openai.com", "chatgpt.com"],
    "Claude": ["claude.ai"],
    "Gemini": ["gemini.google.com"],
    "Copilot": ["copilot.microsoft.com"],
    "Perplexity": ["perplexity.ai"],
}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{2,}")

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "and", "but", "or", "not",
    "this", "that", "these", "those", "it", "its", "i", "you", "me",
    "my", "your", "what", "how", "why", "help", "please", "can",
})


@dataclass
class AIChatSession:
    """Tracks a single AI chat interaction."""
    tool: str
    started_ts: float
    last_ts: float
    prompt_previews: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    MAX_PREVIEWS = 10
    MAX_KEYWORDS = 20

    def add_prompt(self, preview: str, ts: float) -> None:
        self.last_ts = ts
        if preview and preview not in self.prompt_previews:
            self.prompt_previews.append(preview)
            if len(self.prompt_previews) > self.MAX_PREVIEWS:
                self.prompt_previews = self.prompt_previews[-self.MAX_PREVIEWS:]
        # Extract keywords from prompt
        words = _WORD_RE.findall(preview.lower())
        for w in words:
            if w not in _STOP_WORDS and w not in self.keywords:
                self.keywords.append(w)
        if len(self.keywords) > self.MAX_KEYWORDS:
            self.keywords = self.keywords[-self.MAX_KEYWORDS:]


class AIChatObserver:
    """Observes web-based AI chat interactions from browser extension data.

    Implements the WorkflowObserver protocol — check() returns structured updates.
    """

    SESSION_TIMEOUT = 300  # 5 minutes between prompts = new session

    def __init__(self) -> None:
        self._sessions: dict[str, AIChatSession] = {}
        self._pending_updates: list[WorkflowUpdate] = []
        self._lock = threading.Lock()

    def process_input(self, data: dict[str, Any]) -> None:
        """Process extension_input data from the HTTP server.

        Called by ContextServer._process_extension_context when source == "extension_input".
        Thread-safe: called from HTTP handler thread.
        """
        app_hint = data.get("app", data.get("hostname", ""))
        preview = data.get("prompt_preview", "")
        ts = data.get("timestamp", time.time())

        # Identify the AI tool
        tool = self._identify_tool(app_hint)
        if not tool:
            return

        with self._lock:
            # Find or create session
            session = self._sessions.get(tool)
            if session is None or ts - session.last_ts > self.SESSION_TIMEOUT:
                session = AIChatSession(tool=tool, started_ts=ts, last_ts=ts)
                self._sessions[tool] = session

            session.add_prompt(preview, ts)

            # Create breadcrumb
            if preview:
                truncated = preview[:80] + "..." if len(preview) > 80 else preview
                breadcrumb = f"asking {tool}: '{truncated}'"
            else:
                breadcrumb = f"using {tool}"

            self._pending_updates.append(WorkflowUpdate(
                keywords=session.keywords[:10],
                breadcrumb=breadcrumb,
                tool=tool,
                agent_contribution=(tool, f"AI chat session ({len(session.prompt_previews)} prompts)"),
            ))

    def check(self) -> list[WorkflowUpdate]:
        """Return pending updates (WorkflowObserver protocol). Thread-safe."""
        with self._lock:
            updates = self._pending_updates[:]
            self._pending_updates.clear()
        return updates

    def get_active_sessions(self) -> list[dict[str, Any]]:
        """Get currently active AI chat sessions. Thread-safe."""
        now = time.time()
        active = []
        with self._lock:
            for tool, session in self._sessions.items():
                if now - session.last_ts < self.SESSION_TIMEOUT:
                    active.append({
                        "tool": tool,
                        "started_ts": session.started_ts,
                        "last_ts": session.last_ts,
                        "prompt_count": len(session.prompt_previews),
                        "keywords": session.keywords[:10],
                        "duration_seconds": round(session.last_ts - session.started_ts),
                    })
        return active

    def _identify_tool(self, app_or_hostname: str) -> str | None:
        """Identify which AI chat tool from hostname or app name."""
        if not app_or_hostname:
            return None
        lower = app_or_hostname.lower()
        for tool, patterns in _AI_CHAT_PATTERNS.items():
            for pattern in patterns:
                if pattern in lower:
                    return tool
        return None
