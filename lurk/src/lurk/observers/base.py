"""Generic observer interface for feeding structured updates into workflows.

Any source of context (git repos, agent sessions, Slack threads, Google Docs,
email, etc.) can implement the WorkflowObserver protocol. The HTTP server runs
a single observer loop that polls all registered observers and feeds their
updates into the workflow clusterer.

Adding a new observer = implement one class with a `check()` method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class WorkflowUpdate:
    """A structured observation that feeds into a workflow."""
    keywords: list[str] = field(default_factory=list)
    breadcrumb: str = ""
    agent_contribution: tuple[str, str] | None = None  # (tool, summary)
    code_change: str = ""
    research: tuple[str, str] | None = None  # (topic, source)
    document: tuple[str, str] | None = None  # (name, description)
    project: str = ""
    tool: str = ""
    files: list[str] = field(default_factory=list)
    stakeholders: list[tuple[str, str]] = field(default_factory=list)  # (name, context)
    is_break: bool = False
    conversation_extract: Any | None = None  # ConversationExtract from messaging OCR
    document_extract: Any | None = None      # DocumentExtract from document OCR


@runtime_checkable
class WorkflowObserver(Protocol):
    """Protocol for context observers that feed into workflows.

    Implement `check()` to return structured updates. The observer loop
    calls this periodically and feeds results into the workflow clusterer.
    """

    def check(self) -> list[WorkflowUpdate]:
        """Check for new observations. Returns structured updates."""
        ...
