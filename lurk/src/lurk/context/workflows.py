"""Workflow clustering — groups activity into coherent work contexts.

Workflows are the core unit of shared context in lurk. They represent
a coherent thread of work (e.g. "JWT auth investigation", "lurk project",
"Q1 budget review") that spans multiple tools, apps, and time periods.

Workflows are fed by:
- Enriched events from the daemon (window titles, app switches)
- Viewport captures from the extension (page content, typing)
- Both flow through the same clustering engine

Uses header text, page titles, file names, and extracted keywords to
automatically cluster activity into workflows. No LLM required.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


# Common stop words to exclude from topic extraction
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "and", "but", "or",
    "not", "no", "nor", "so", "yet", "both", "either", "neither", "each",
    "every", "all", "any", "few", "more", "most", "other", "some", "such",
    "than", "too", "very", "just", "also", "how", "what", "which", "who",
    "when", "where", "why", "this", "that", "these", "those", "it", "its",
    "i", "you", "he", "she", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "our", "their", "about", "up", "out", "if", "then",
    "new", "get", "got", "use", "using", "used", "one", "two",
    "google", "docs", "sheets", "chrome", "stackoverflow", "github",
})

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{2,}")


@dataclass
class Workflow:
    """A cluster of related activity forming a coherent work context.

    A workflow spans tools — it might include Claude Code, a Google Doc,
    a Stack Overflow search, and a ChatGPT conversation, all connected
    by topic overlap.
    """
    id: int
    created_ts: float
    updated_ts: float
    topic_keywords: list[str]
    label: str | None = None
    capture_count: int = 0
    event_count: int = 0  # enriched events assigned to this workflow
    is_active: bool = True
    status: str = "active"  # active | paused | completed
    tools: list[str] = field(default_factory=list)  # apps/sites that contributed
    projects: list[str] = field(default_factory=list)  # code projects involved
    files: list[str] = field(default_factory=list)  # files touched
    tickets: list[str] = field(default_factory=list)  # tickets referenced
    key_decisions: list[str] = field(default_factory=list)  # user-typed intent summaries

    @property
    def duration_seconds(self) -> float:
        return self.updated_ts - self.created_ts

    @property
    def duration_label(self) -> str:
        mins = int(self.duration_seconds / 60)
        if mins < 60:
            return f"{mins}m"
        hours = mins // 60
        remaining = mins % 60
        return f"{hours}h {remaining}m"

    def overlap_score(self, keywords: list[str]) -> float:
        """Score how well a set of keywords matches this workflow."""
        if not self.topic_keywords or not keywords:
            return 0.0
        my_set = set(w.lower() for w in self.topic_keywords)
        their_set = set(w.lower() for w in keywords)
        intersection = my_set & their_set
        if not intersection:
            return 0.0
        # Jaccard-ish but weighted toward the smaller set
        return len(intersection) / min(len(my_set), len(their_set))

    def add_tool(self, tool: str) -> None:
        if tool and tool not in self.tools:
            self.tools.append(tool)
            if len(self.tools) > 20:
                self.tools = self.tools[-20:]

    def add_project(self, project: str) -> None:
        if project and project not in self.projects:
            self.projects.append(project)

    def add_file(self, file: str) -> None:
        if file and file not in self.files:
            self.files.append(file)
            if len(self.files) > 50:
                self.files = self.files[-50:]

    def add_ticket(self, ticket: str) -> None:
        if ticket and ticket not in self.tickets:
            self.tickets.append(ticket)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
            "duration": self.duration_label,
            "topic_keywords": self.topic_keywords[:10],
            "tools": self.tools,
            "projects": self.projects,
            "files_count": len(self.files),
            "tickets": self.tickets,
            "capture_count": self.capture_count,
            "event_count": self.event_count,
            "is_active": self.is_active,
        }


class WorkflowClusterer:
    """Clusters captures into workflows based on topic overlap."""

    MERGE_THRESHOLD = 0.3  # minimum keyword overlap to join a workflow
    STALE_MINUTES = 60  # workflows with no captures for this long become inactive

    def __init__(self) -> None:
        self._workflows: list[Workflow] = []
        self._next_id = 1

    def load_from_db(self, conn) -> None:
        """Load active workflows from the database."""
        try:
            cursor = conn.execute(
                "SELECT * FROM workflows WHERE is_active = 1 ORDER BY updated_ts DESC LIMIT 20"
            )
            for row in cursor.fetchall():
                d = dict(row)
                keywords = json.loads(d["topic_keywords"]) if d["topic_keywords"] else []
                wf = Workflow(
                    id=d["id"],
                    created_ts=d["created_ts"],
                    updated_ts=d["updated_ts"],
                    topic_keywords=keywords,
                    label=d.get("label"),
                    capture_count=d.get("capture_count", 0),
                    is_active=bool(d.get("is_active", 1)),
                )
                self._workflows.append(wf)
                if wf.id >= self._next_id:
                    self._next_id = wf.id + 1
        except Exception:
            pass  # Table may not exist yet

    def process_enriched_event(self, event: dict, conn=None) -> int | None:
        """Process an enriched event from the daemon and assign to a workflow.

        This is how the core observer feeds workflows — every enriched event
        (window title change, app switch, etc.) gets routed here.
        """
        # Build keywords from event fields
        keywords = []
        for field in ("title", "file", "project", "topic", "document_name", "ticket"):
            val = event.get(field)
            if val and isinstance(val, str):
                words = _WORD_RE.findall(val.lower())
                keywords.extend(w for w in words if w not in _STOP_WORDS and len(w) > 2)

        if not keywords:
            return None

        # Deduplicate
        keywords = list(dict.fromkeys(keywords))[:15]

        # Find or create workflow
        workflow_id = self._match_or_create(keywords, conn)
        wf = self.get_workflow(workflow_id)
        if wf:
            wf.event_count += 1
            app = event.get("app", "")
            if app:
                wf.add_tool(app)
            project = event.get("project")
            if project:
                wf.add_project(project)
            file = event.get("file")
            if file:
                wf.add_file(file)
            ticket = event.get("ticket")
            if ticket:
                wf.add_ticket(ticket)
            self._save_workflow(wf, conn)

        return workflow_id

    def assign_workflow(self, capture_data: dict, conn=None) -> int:
        """Assign a capture to a workflow. Returns workflow ID."""
        keywords = extract_keywords(capture_data)
        if not keywords:
            active = self.get_active_workflow()
            return active.id if active else self._create_workflow(keywords, conn)

        workflow_id = self._match_or_create(keywords, conn)
        wf = self.get_workflow(workflow_id)
        if wf:
            wf.capture_count += 1
            # Track tool from capture
            hostname = capture_data.get("hostname", "")
            app = capture_data.get("app", "")
            if hostname:
                wf.add_tool(hostname)
            if app:
                wf.add_tool(app)
            self._save_workflow(wf, conn)
        return workflow_id

    def _match_or_create(self, keywords: list[str], conn=None) -> int:
        """Find best matching workflow or create a new one."""
        now = time.time()
        best_wf = None
        best_score = 0.0

        for wf in self._workflows:
            if not wf.is_active or wf.status == "completed":
                continue
            if now - wf.updated_ts > self.STALE_MINUTES * 60:
                wf.is_active = False
                continue
            score = wf.overlap_score(keywords)
            if score > best_score:
                best_score = score
                best_wf = wf

        if best_wf and best_score >= self.MERGE_THRESHOLD:
            best_wf.updated_ts = now
            # Expand keywords
            existing = set(w.lower() for w in best_wf.topic_keywords)
            for kw in keywords:
                if kw.lower() not in existing:
                    best_wf.topic_keywords.append(kw)
            if len(best_wf.topic_keywords) > 30:
                best_wf.topic_keywords = best_wf.topic_keywords[-30:]
            self._save_workflow(best_wf, conn)
            return best_wf.id
        else:
            return self._create_workflow(keywords, conn)

    def complete_workflow(self, workflow_id: int, conn=None) -> bool:
        """Mark a workflow as completed."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            return False
        wf.status = "completed"
        wf.is_active = False
        self._save_workflow(wf, conn)
        return True

    def reopen_workflow(self, workflow_id: int, conn=None) -> bool:
        """Reopen a completed workflow."""
        wf = self.get_workflow(workflow_id)
        if not wf:
            return False
        wf.status = "active"
        wf.is_active = True
        wf.updated_ts = time.time()
        self._save_workflow(wf, conn)
        return True

    def list_workflows(self, include_completed: bool = False) -> list[Workflow]:
        """List workflows, most recent first."""
        now = time.time()
        results = []
        for wf in self._workflows:
            if not include_completed and wf.status == "completed":
                continue
            # Auto-deactivate stale workflows (but don't complete them)
            if wf.is_active and now - wf.updated_ts > self.STALE_MINUTES * 60:
                wf.is_active = False
            results.append(wf)
        results.sort(key=lambda w: w.updated_ts, reverse=True)
        return results

    def get_active_workflow(self) -> Workflow | None:
        """Get the most recently updated active workflow."""
        now = time.time()
        active = [
            wf for wf in self._workflows
            if wf.is_active and now - wf.updated_ts < self.STALE_MINUTES * 60
        ]
        if not active:
            return None
        return max(active, key=lambda wf: wf.updated_ts)

    def get_workflow(self, workflow_id: int) -> Workflow | None:
        for wf in self._workflows:
            if wf.id == workflow_id:
                return wf
        return None

    def _create_workflow(self, keywords: list[str], conn=None) -> int:
        now = time.time()
        wf = Workflow(
            id=self._next_id,
            created_ts=now,
            updated_ts=now,
            topic_keywords=keywords,
            label=_generate_label(keywords),
            capture_count=1,
            is_active=True,
        )
        self._next_id += 1
        self._workflows.append(wf)
        # Keep bounded
        if len(self._workflows) > 50:
            self._workflows = sorted(
                self._workflows, key=lambda w: w.updated_ts, reverse=True
            )[:50]
        self._save_workflow(wf, conn)
        return wf.id

    def _save_workflow(self, wf: Workflow, conn=None) -> None:
        if conn is None:
            return
        try:
            conn.execute(
                """INSERT OR REPLACE INTO workflows
                (id, created_ts, updated_ts, topic_keywords, label, capture_count, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    wf.id, wf.created_ts, wf.updated_ts,
                    json.dumps(wf.topic_keywords), wf.label,
                    wf.capture_count, int(wf.is_active),
                ),
            )
            conn.commit()
        except Exception:
            pass


def extract_keywords(capture_data: dict) -> list[str]:
    """Extract topic keywords from a capture for workflow clustering."""
    text_sources = []

    # Page title — high signal
    title = capture_data.get("page_title", "")
    if title:
        text_sources.append(title)
        text_sources.append(title)  # double-weight titles

    # Headers — high signal
    headers = capture_data.get("headers")
    if isinstance(headers, list):
        for h in headers:
            text = h.get("text", "") if isinstance(h, dict) else str(h)
            if text:
                text_sources.append(text)
    elif isinstance(headers, str):
        text_sources.append(headers)

    # URL path segments
    url = capture_data.get("url", "")
    if url:
        # Extract meaningful path segments
        path = url.split("//", 1)[-1].split("/")[1:]
        for seg in path:
            seg = seg.split("?")[0].split("#")[0]
            if seg and len(seg) > 2 and not seg.isdigit():
                text_sources.append(seg.replace("-", " ").replace("_", " "))

    # Typing text — very high signal for intent
    typing = capture_data.get("typing_text") or capture_data.get("text_preview", "")
    if typing:
        text_sources.append(typing)
        text_sources.append(typing)  # double-weight

    # Meta description
    meta = capture_data.get("meta", {})
    if isinstance(meta, dict):
        desc = meta.get("description", "")
        if desc:
            text_sources.append(desc)

    # Count words across all sources
    combined = " ".join(text_sources).lower()
    words = _WORD_RE.findall(combined)
    filtered = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 2]

    # Return top keywords by frequency
    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(15)]


def _generate_label(keywords: list[str]) -> str | None:
    """Generate a human-readable label from keywords."""
    if not keywords:
        return None
    # Take top 3 keywords as label
    return " / ".join(keywords[:3])
