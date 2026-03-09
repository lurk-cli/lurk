"""Workflow clustering — groups activity into coherent work contexts.

Workflows are the core unit of shared context in lurk. They represent
a coherent thread of work (e.g. "JWT auth investigation", "lurk project",
"Q1 budget review") that spans multiple tools, apps, and time periods.

Every observer feeds into workflows:
- Window titles → breadcrumbs ("reading email about Project Alpha")
- Git watcher → code contributions ("added session_watcher.py")
- Session watcher → agent summaries ("Claude Code built the HTTP server")
- Extension captures → research/content context

The workflow accumulates this into an evolving context that any consuming
agent can use to understand what's being worked on and why.
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

_BREAK_APPS = frozenset({
    # Video/streaming
    "youtube", "netflix", "hulu", "disney+", "prime video", "twitch",
    "vlc", "iina", "plex", "hbo max", "apple tv",
    # Gaming
    "steam", "epic games", "battle.net", "origin", "gog galaxy",
    "minecraft", "league of legends", "valorant", "fortnite",
    # Social media (non-work)
    "tiktok", "instagram", "snapchat", "reddit",
    # Shopping
    "amazon shopping",
    # Music (background, not workflow-relevant)
    "spotify", "apple music", "music",
})

# Max breadcrumbs/contributions per workflow
MAX_BREADCRUMBS = 50
MAX_CONTRIBUTIONS = 20
MAX_RESEARCH = 20
MAX_DOCUMENTS = 30


@dataclass
class Workflow:
    """A cluster of related activity forming a coherent work context.

    A workflow spans tools — it might include Claude Code, a Google Doc,
    a Stack Overflow search, and a ChatGPT conversation, all connected
    by topic overlap.

    The workflow accumulates structured context from all observers so that
    at any point, a consuming agent can get a synthesized understanding of
    what's being worked on.
    """
    id: int
    created_ts: float
    updated_ts: float
    topic_keywords: list[str]
    label: str | None = None
    capture_count: int = 0
    event_count: int = 0
    is_active: bool = True
    status: str = "active"  # active | paused | completed
    tools: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)

    # Artifact lifecycle references
    artifact_refs: list[dict] = field(default_factory=list)

    # Inferred decisions from activity patterns
    decisions_inferred: list[dict] = field(default_factory=list)

    # --- Prompt memory ---
    last_prompt: str = ""
    last_prompt_ts: float = 0
    prompt_version: int = 0

    # --- Accumulated context from observers ---

    # Breadcrumbs: natural language trail of what happened
    # e.g. "reading email about Project Alpha", "editing Q3 Revenue spreadsheet"
    breadcrumbs: list[dict] = field(default_factory=list)

    # Agent contributions: what each tool/agent produced
    # e.g. {"Claude Code": "built session watcher and HTTP server endpoints"}
    agent_contributions: dict[str, str] = field(default_factory=dict)

    # Research trail: topics researched with sources
    # e.g. [{"topic": "SaaS market size", "source": "google.com"}]
    research: list[dict[str, str]] = field(default_factory=list)

    # Code changes summary: what was actually built/changed
    # e.g. ["added observers/session_watcher.py", "modified server/http.py to add endpoints"]
    code_changes: list[str] = field(default_factory=list)

    # Documents involved: specific doc names and what they contain
    # e.g. {"Q3 Revenue Forecast": "spreadsheet with revenue projections"}
    documents: dict[str, str] = field(default_factory=dict)

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
        return len(intersection) / min(len(my_set), len(their_set))

    # --- Context accumulation methods ---

    def add_breadcrumb(self, description: str, ts: float | None = None) -> None:
        """Record what the user was doing — dedupes consecutive identical entries."""
        ts = ts if ts is not None else time.time()

        if self.breadcrumbs:
            prev = self.breadcrumbs[-1]
            # Backward compat: wrap plain str entries
            if isinstance(prev, str):
                prev = {"ts": ts, "description": prev, "duration_seconds": 0.0, "repeat_count": 1}
                self.breadcrumbs[-1] = prev
            if prev["description"] == description:
                prev["repeat_count"] = prev.get("repeat_count", 1) + 1
                prev["ts"] = ts
                return
            # Compute duration on previous entry
            prev["duration_seconds"] = ts - prev["ts"]

        self.breadcrumbs.append({
            "ts": ts,
            "description": description,
            "duration_seconds": 0.0,
            "repeat_count": 1,
        })
        if len(self.breadcrumbs) > MAX_BREADCRUMBS:
            self.breadcrumbs = self.breadcrumbs[-MAX_BREADCRUMBS:]

    def _format_breadcrumb_narrative(self, breadcrumbs: list[dict] | None = None, max_items: int = 6) -> str:
        """Format breadcrumbs as a readable narrative trail."""
        items = (breadcrumbs if breadcrumbs is not None else self.breadcrumbs)[-max_items:]
        if not items:
            return ""

        segments: list[str] = []
        quick: list[str] = []

        for entry in items:
            # Backward compat: wrap plain str entries
            if isinstance(entry, str):
                entry = {"ts": 0, "description": entry, "duration_seconds": 0.0, "repeat_count": 1}
            dur = entry.get("duration_seconds", 0.0)
            desc = entry.get("description", "")

            if dur >= 30:
                # Flush any pending quick lookups first
                if quick:
                    segments.append("quick lookups: " + ", ".join(quick))
                    quick = []
                if dur >= 60:
                    minutes = int(dur / 60)
                    segments.append(f"{desc} ({minutes}m)")
                else:
                    segments.append(f"{desc} ({int(dur)}s)")
            else:
                quick.append(desc)

        # Flush remaining quick lookups
        if quick:
            segments.append("quick lookups: " + ", ".join(quick))

        return " → ".join(segments)

    def add_agent_contribution(self, tool: str, summary: str) -> None:
        """Record what an agent contributed to this workflow."""
        self.agent_contributions[tool] = summary  # latest overwrites
        self.add_tool(tool)

    def add_research(self, topic: str, source: str = "") -> None:
        """Record a research action."""
        # Dedupe by topic
        for r in self.research:
            if r.get("topic") == topic:
                return
        self.research.append({"topic": topic, "source": source})
        if len(self.research) > MAX_RESEARCH:
            self.research = self.research[-MAX_RESEARCH:]

    def add_code_change(self, description: str) -> None:
        """Record a code change."""
        if description not in self.code_changes:
            self.code_changes.append(description)
            if len(self.code_changes) > MAX_CONTRIBUTIONS:
                self.code_changes = self.code_changes[-MAX_CONTRIBUTIONS:]

    def add_document(self, name: str, description: str = "") -> None:
        """Record a document involved in this workflow."""
        self.documents[name] = description or self.documents.get(name, "")
        if len(self.documents) > MAX_DOCUMENTS:
            # Keep most recent entries (dicts are insertion-ordered in Python 3.7+)
            keys = list(self.documents.keys())
            for k in keys[:-MAX_DOCUMENTS]:
                del self.documents[k]

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

    def add_feedback(self, feedback_type: str, content: str) -> None:
        """Record structured feedback from an agent.

        Types: decision, finding, blocker, summary, question
        """
        if feedback_type == "decision":
            if content not in self.key_decisions:
                self.key_decisions.append(content)
                if len(self.key_decisions) > MAX_CONTRIBUTIONS:
                    self.key_decisions = self.key_decisions[-MAX_CONTRIBUTIONS:]
        elif feedback_type == "finding":
            self.add_research(content)
        elif feedback_type == "blocker":
            self.add_breadcrumb(f"BLOCKED: {content}")
        elif feedback_type == "summary":
            # Overwrite the latest agent contribution with a summary
            self.add_agent_contribution("agent_summary", content)
        elif feedback_type == "question":
            self.add_breadcrumb(f"Open question: {content}")
        else:
            self.add_breadcrumb(content)
        self.updated_ts = time.time()

    def add_artifact_ref(self, name: str, artifact_type: str, status: str, last_edit: float = 0) -> None:
        """Record an artifact reference in this workflow."""
        # Update existing or add new
        for ref in self.artifact_refs:
            if ref.get("name") == name:
                ref["status"] = status
                ref["last_edit"] = last_edit
                return
        self.artifact_refs.append({
            "name": name, "type": artifact_type,
            "status": status, "last_edit": last_edit,
        })
        if len(self.artifact_refs) > 20:
            self.artifact_refs = self.artifact_refs[-20:]

    def add_inferred_decision(self, description: str, confidence: float, ts: float) -> None:
        """Record an inferred decision in this workflow."""
        self.decisions_inferred.append({
            "description": description, "confidence": confidence, "ts": ts,
        })
        if len(self.decisions_inferred) > 20:
            self.decisions_inferred = self.decisions_inferred[-20:]

    # --- Context output ---

    def context_snapshot(self) -> dict[str, Any]:
        """Get the full accumulated context for this workflow.

        This is what gets fed to the LLM or rules-based prompt generator
        to produce a synthesized prompt for consuming agents.
        """
        return {
            "id": self.id,
            "label": self.label,
            "status": self.status,
            "duration": self.duration_label,
            "keywords": self.topic_keywords[:10],
            "tools": self.tools,
            "projects": self.projects,
            "tickets": self.tickets,
            "breadcrumbs": self.breadcrumbs[-10:],
            "agent_contributions": self.agent_contributions,
            "research": self.research[-5:],
            "code_changes": self.code_changes[-5:],
            "documents": dict(list(self.documents.items())[-5:]),
            "key_decisions": self.key_decisions[-5:],
            "artifact_refs": self.artifact_refs[-5:],
            "decisions_inferred": self.decisions_inferred[-5:],
            "files_count": len(self.files),
            "prompt_version": self.prompt_version,
        }

    def generate_prompt(self, max_chars: int = 1200) -> str:
        """Generate a natural language context prompt for this workflow.

        This is the rules-based version. The LLM-enhanced version uses
        context_snapshot() as input and synthesizes a better prompt.

        Implements prompt memory: if the new prompt is substantially similar
        to the last one, returns the cached version. When it changes
        meaningfully, increments the version counter.
        """
        parts: list[str] = []

        # Lead with what's being worked on
        if self.label:
            parts.append(f"The user is working on: {self.label}.")
        if self.projects:
            parts.append(f"Project: {', '.join(self.projects[:3])}.")

        # Key decisions (fed back by agents)
        if self.key_decisions:
            parts.append("Key decisions: " + "; ".join(self.key_decisions[-3:]) + ".")

        # What agents have contributed
        for tool, summary in self.agent_contributions.items():
            parts.append(f"{tool}: {summary}")

        # Activity trail — connect the dots
        if self.breadcrumbs:
            trail = self._format_breadcrumb_narrative()
            if trail:
                parts.append(f"Recent activity: {trail}.")

        # Research
        if self.research:
            topics = [r["topic"] for r in self.research[-3:]]
            parts.append(f"Researched: {', '.join(topics)}.")

        # Code changes
        if self.code_changes:
            parts.append("Code changes: " + "; ".join(self.code_changes[-3:]) + ".")

        # Documents
        if self.documents:
            for name, desc in list(self.documents.items())[-3:]:
                if desc:
                    parts.append(f"Document \"{name}\": {desc}.")
                else:
                    parts.append(f"Working with \"{name}\".")

        # Artifacts lifecycle
        if self.artifact_refs:
            for ref in self.artifact_refs[-3:]:
                parts.append(f"\"{ref['name']}\" ({ref['type']}, {ref['status']}).")

        # Inferred decisions
        if self.decisions_inferred:
            descs = [d["description"] for d in self.decisions_inferred[-3:] if d.get("confidence", 0) >= 0.6]
            if descs:
                parts.append("Inferred: " + "; ".join(descs) + ".")

        # Tickets
        if self.tickets:
            parts.append(f"Related tickets: {', '.join(self.tickets[-3:])}.")

        # Duration
        if self.duration_seconds > 300:
            parts.append(f"Active for {self.duration_label}.")

        result = " ".join(parts)[:max_chars]

        # Prompt memory: check if meaningfully different from last prompt
        if self.last_prompt and self._prompt_similarity(result, self.last_prompt) > 0.85:
            return self.last_prompt

        # Prompt changed meaningfully — update memory
        self.last_prompt = result
        self.last_prompt_ts = time.time()
        self.prompt_version += 1
        return result

    def _prompt_similarity(self, a: str, b: str) -> float:
        """Quick word-level similarity check between two prompts."""
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        return len(intersection) / max(len(words_a), len(words_b))

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
            "breadcrumbs": self.breadcrumbs[-5:],
            "agent_contributions": self.agent_contributions,
            "research": self.research[-3:],
            "code_changes": self.code_changes[-3:],
            "documents": dict(list(self.documents.items())[-3:]),
            "key_decisions": self.key_decisions[-3:],
            "artifact_refs": self.artifact_refs[-3:],
            "decisions_inferred": self.decisions_inferred[-3:],
            "prompt_version": self.prompt_version,
        }


class WorkflowClusterer:
    """Clusters captures into workflows based on topic overlap."""

    MERGE_THRESHOLD = 0.3
    STALE_MINUTES = 60

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
            pass

    def process_enriched_event(self, event: dict, conn=None) -> int | None:
        """Process an enriched event and assign to a workflow.

        Also extracts structured context from the event and adds it
        to the workflow — breadcrumbs, documents, research, etc.
        """
        # Break apps: don't pollute workflow keywords, but keep the thread alive
        app = event.get("app", "")
        if app.lower().strip() in _BREAK_APPS:
            active = self.get_active_workflow()
            if active:
                active.add_breadcrumb(f"break ({app})")
                self._save_workflow(active, conn)
                return active.id
            return None

        keywords = []
        for fld in ("title", "file", "project", "topic", "document_name", "ticket"):
            val = event.get(fld)
            if val and isinstance(val, str):
                words = _WORD_RE.findall(val.lower())
                keywords.extend(w for w in words if w not in _STOP_WORDS and len(w) > 2)

        if not keywords:
            return None

        keywords = list(dict.fromkeys(keywords))[:15]

        workflow_id = self._match_or_create(keywords, conn)
        wf = self.get_workflow(workflow_id)
        if not wf:
            return workflow_id

        wf.event_count += 1

        # --- Feed structured context into the workflow ---

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

        # Add breadcrumb — natural language description of this event
        breadcrumb = _describe_event(event)
        if breadcrumb:
            wf.add_breadcrumb(breadcrumb)

        # Track documents
        doc_name = event.get("document_name")
        if doc_name:
            sub = event.get("sub_activity", "")
            if sub == "spreadsheet":
                wf.add_document(doc_name, "spreadsheet")
            elif sub == "presentation":
                wf.add_document(doc_name, "presentation")
            else:
                wf.add_document(doc_name)

        # Track research
        activity = event.get("activity", "")
        topic = event.get("topic")
        if activity == "researching" and topic:
            domain = event.get("url_domain", "")
            wf.add_research(topic, domain)

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
            # Temporal affinity: recent activity in workflow boosts match
            # This stitches cross-app workflows (e.g., Claude Code -> ChatGPT research)
            recency = max(0, 1 - (now - wf.updated_ts) / 120)  # decays over 2 min
            score = score + recency * 0.15  # boost recent workflows
            if score > best_score:
                best_score = score
                best_wf = wf

        if best_wf and best_score >= self.MERGE_THRESHOLD:
            best_wf.updated_ts = now
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
        wf = self.get_workflow(workflow_id)
        if not wf:
            return False
        wf.status = "completed"
        wf.is_active = False
        self._save_workflow(wf, conn)
        return True

    def add_feedback(self, feedback_type: str, content: str, workflow_id: int | None = None, conn=None) -> dict:
        """Add structured feedback from an agent to a workflow.

        If workflow_id is None, uses the active workflow.
        Returns {"ok": True, "workflow_id": int} or {"error": str}.
        """
        if workflow_id is not None:
            wf = self.get_workflow(workflow_id)
        else:
            wf = self.get_active_workflow()

        if not wf:
            return {"error": "No active workflow found. Start working on something first."}

        wf.add_feedback(feedback_type, content)
        self._save_workflow(wf, conn)
        return {"ok": True, "workflow_id": wf.id, "type": feedback_type}

    def reopen_workflow(self, workflow_id: int, conn=None) -> bool:
        wf = self.get_workflow(workflow_id)
        if not wf:
            return False
        wf.status = "active"
        wf.is_active = True
        wf.updated_ts = time.time()
        self._save_workflow(wf, conn)
        return True

    def list_workflows(self, include_completed: bool = False) -> list[Workflow]:
        now = time.time()
        results = []
        for wf in self._workflows:
            if not include_completed and wf.status == "completed":
                continue
            if wf.is_active and now - wf.updated_ts > self.STALE_MINUTES * 60:
                wf.is_active = False
            results.append(wf)
        results.sort(key=lambda w: w.updated_ts, reverse=True)
        return results

    def get_active_workflow(self) -> Workflow | None:
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


def _describe_event(event: dict) -> str:
    """Turn an enriched event into a natural breadcrumb for the workflow."""
    app = event.get("app", "")
    if app.lower().strip() in _BREAK_APPS:
        return ""
    activity = event.get("activity", "")
    sub = event.get("sub_activity", "")
    doc = event.get("document_name")
    topic = event.get("topic")
    file = event.get("file")
    project = event.get("project")

    if doc:
        if sub == "spreadsheet":
            return f"working on spreadsheet \"{doc}\""
        if sub == "presentation":
            return f"working on presentation \"{doc}\""
        return f"working on \"{doc}\""

    if sub == "email_reading" and topic:
        return f"reading email about \"{topic}\""
    if sub == "email_composing":
        return "composing an email"

    if activity == "researching" and topic:
        return f"researching \"{topic}\""

    if activity == "coding" and file and project:
        return f"editing {file} in {project}"

    if sub == "code_review" and topic:
        return f"reviewing \"{topic}\""

    if activity == "communicating" and topic:
        return f"conversation about \"{topic}\""

    if activity == "browsing" and topic:
        return f"looking at \"{topic}\""

    # Screen-observed AI chat activity (e.g., on secondary display)
    app_lower = app.lower()
    _ai_chat_indicators = {"chatgpt": "ChatGPT", "claude": "Claude", "cursor": "Cursor", "copilot": "Copilot"}
    for indicator, name in _ai_chat_indicators.items():
        if indicator in app_lower:
            display_note = ""
            if event.get("monitor_id") is not None and event.get("is_secondary"):
                display_note = " on secondary display"
            if activity == "ai_chat" or "interacting" in activity:
                return f"researching in {name}{display_note}"
            if topic:
                return f"viewing {name}: \"{topic}\"{display_note}"
            return f"using {name}{display_note}"

    return ""


def extract_keywords(capture_data: dict) -> list[str]:
    """Extract topic keywords from a capture for workflow clustering."""
    text_sources = []

    title = capture_data.get("page_title", "")
    if title:
        text_sources.append(title)
        text_sources.append(title)

    headers = capture_data.get("headers")
    if isinstance(headers, list):
        for h in headers:
            text = h.get("text", "") if isinstance(h, dict) else str(h)
            if text:
                text_sources.append(text)
    elif isinstance(headers, str):
        text_sources.append(headers)

    url = capture_data.get("url", "")
    if url:
        path = url.split("//", 1)[-1].split("/")[1:]
        for seg in path:
            seg = seg.split("?")[0].split("#")[0]
            if seg and len(seg) > 2 and not seg.isdigit():
                text_sources.append(seg.replace("-", " ").replace("_", " "))

    typing = capture_data.get("typing_text") or capture_data.get("text_preview", "")
    if typing:
        text_sources.append(typing)
        text_sources.append(typing)

    meta = capture_data.get("meta", {})
    if isinstance(meta, dict):
        desc = meta.get("description", "")
        if desc:
            text_sources.append(desc)

    combined = " ".join(text_sources).lower()
    words = _WORD_RE.findall(combined)
    filtered = [w for w in words if w.lower() not in _STOP_WORDS and len(w) > 2]

    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(15)]


def _generate_label(keywords: list[str]) -> str | None:
    if not keywords:
        return None
    return " / ".join(keywords[:3])
