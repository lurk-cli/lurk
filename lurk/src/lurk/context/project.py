"""Project graph — tracks known projects and their associations."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class ProjectInfo:
    """Information about a known project."""
    name: str
    files: list[str] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    last_active: float = field(default_factory=time.time)
    total_seconds: float = 0

    MAX_FILES = 50
    MAX_TICKETS = 20

    def add_file(self, file: str) -> None:
        if file and file not in self.files:
            self.files.append(file)
            if len(self.files) > self.MAX_FILES:
                self.files = self.files[-self.MAX_FILES:]

    def add_ticket(self, ticket: str) -> None:
        if ticket and ticket not in self.tickets:
            self.tickets.append(ticket)
            if len(self.tickets) > self.MAX_TICKETS:
                self.tickets = self.tickets[-self.MAX_TICKETS:]

    def add_tool(self, tool: str) -> None:
        if tool and tool not in self.tools:
            self.tools.append(tool)

    def add_language(self, lang: str) -> None:
        if lang and lang not in self.languages:
            self.languages.append(lang)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "files": self.files,
            "tickets": self.tickets,
            "tools": self.tools,
            "languages": self.languages,
            "last_active": self.last_active,
            "total_seconds": round(self.total_seconds),
        }


class ProjectGraph:
    """Maintains a graph of known projects."""

    MAX_PROJECTS = 20

    def __init__(self) -> None:
        self.projects: dict[str, ProjectInfo] = {}
        self._last_project: str | None = None
        self._last_project_ts: float = 0

    def update(self, event: dict) -> None:
        """Update project graph from an enriched event."""
        project = event.get("project")
        if not project:
            return

        ts = event.get("ts", time.time())

        # Get or create project
        if project not in self.projects:
            if len(self.projects) >= self.MAX_PROJECTS:
                # Evict least recently active
                oldest = min(self.projects.values(), key=lambda p: p.last_active)
                del self.projects[oldest.name]
            self.projects[project] = ProjectInfo(name=project)

        info = self.projects[project]
        info.last_active = ts

        # Track time spent
        if self._last_project == project and self._last_project_ts:
            info.total_seconds += ts - self._last_project_ts
        self._last_project = project
        self._last_project_ts = ts

        # Update associations
        if event.get("file"):
            info.add_file(event["file"])
        if event.get("ticket"):
            info.add_ticket(event["ticket"])
        if event.get("app"):
            info.add_tool(event["app"])
        if event.get("language"):
            info.add_language(event["language"])

    def get(self, name: str, default: dict | None = None) -> dict:
        """Get project info as dict."""
        if name in self.projects:
            return self.projects[name].to_dict()
        return default or {}

    def to_dict(self) -> dict:
        """Return all projects sorted by last active."""
        sorted_projects = sorted(
            self.projects.values(),
            key=lambda p: p.last_active,
            reverse=True,
        )
        return {p.name: p.to_dict() for p in sorted_projects}
