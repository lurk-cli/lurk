"""Workstream management — LLM-inferred coherent threads of work.

Workstreams sit above workflows. While workflows are keyword-overlap clusters,
workstreams represent what the user is actually trying to accomplish — inferred
by an LLM from accumulated signals (events, conversations, documents, git diffs).

The WorkstreamManager accumulates raw signals into a staging buffer. An external
LLM engine periodically reads the buffer, infers workstream updates, and writes
back results via `apply_llm_results()`.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("lurk.workstreams")

# Staging buffer cap — drop oldest when exceeded
MAX_STAGING_BUFFER = 200


@dataclass
class Workstream:
    """A coherent thread of work inferred by LLM from observed activity.

    Unlike workflows (keyword clusters), workstreams represent goals:
    "implement JWT auth", "prepare Q1 board deck", "debug memory leak".
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    inferred_goal: str = ""
    status: str = "active"           # active | paused | stale | completed
    persona: str = "general"         # developer | pm | designer | marketer | general
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    last_llm_refresh_ts: float = 0.0
    confidence: float = 0.5          # 0-1

    # Core content
    primary_artifacts: list[str] = field(default_factory=list)
    supporting_research: list[dict] = field(default_factory=list)
    related_communications: list[dict] = field(default_factory=list)
    key_decisions: list[str] = field(default_factory=list)
    current_state: str = ""
    key_people: list[str] = field(default_factory=list)

    # Signal sources
    event_ids: list[int] = field(default_factory=list)
    workflow_ids: list[int] = field(default_factory=list)
    git_branches: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)

    # Scoring
    activity_score: float = 1.0
    event_count: int = 0

    def to_dict(self) -> dict:
        """Serialize for JSON/API output."""
        return {
            "id": self.id,
            "inferred_goal": self.inferred_goal,
            "status": self.status,
            "persona": self.persona,
            "created_ts": self.created_ts,
            "updated_ts": self.updated_ts,
            "last_llm_refresh_ts": self.last_llm_refresh_ts,
            "confidence": round(self.confidence, 3),
            "primary_artifacts": self.primary_artifacts[:10],
            "supporting_research": self.supporting_research[:10],
            "related_communications": self.related_communications[:10],
            "key_decisions": self.key_decisions[:10],
            "current_state": self.current_state,
            "key_people": self.key_people[:10],
            "event_count": self.event_count,
            "workflow_ids": self.workflow_ids[:20],
            "git_branches": self.git_branches[:10],
            "projects": self.projects[:10],
            "tools_used": self.tools_used[:10],
            "activity_score": round(self.activity_score, 3),
        }

    def decay(self, now: float | None = None) -> None:
        """Reduce activity score based on time since last update.

        Uses exponential decay with a half-life of ~30 minutes.
        """
        now = now or time.time()
        elapsed = now - self.updated_ts
        if elapsed <= 0:
            return
        # Half-life of 1800 seconds (30 min)
        half_life = 1800.0
        self.activity_score *= math.exp(-0.693 * elapsed / half_life)
        # Floor at a small positive value
        if self.activity_score < 0.01:
            self.activity_score = 0.01

    def boost(self, amount: float = 0.3) -> None:
        """Boost activity score when new events match this workstream."""
        self.activity_score = min(2.0, self.activity_score + amount)
        self.updated_ts = time.time()


class WorkstreamManager:
    """Manages active workstreams, ingests signals, triggers LLM refresh."""

    MAX_ACTIVE = 10
    DECAY_INTERVAL = 300        # 5 minutes
    STALE_THRESHOLD = 7200      # 2 hours
    PAUSE_THRESHOLD = 1800      # 30 minutes

    def __init__(self, db=None):
        self.workstreams: list[Workstream] = []
        self._staging_buffer: list[dict] = []  # raw signals awaiting LLM processing
        self._db = db
        self._last_decay_ts: float = 0.0
        # Load from DB on init if available
        if db:
            self._load_from_db()

    def ingest_event(self, event: dict) -> None:
        """Add an enriched event to the staging buffer."""
        self._staging_buffer.append({
            "type": "event",
            "data": event,
            "ts": time.time(),
        })
        self._trim_staging_buffer()
        self._maybe_decay()

    def ingest_conversation(self, extract: Any) -> None:
        """Add a ConversationExtract to staging."""
        data = extract
        if hasattr(extract, "to_dict"):
            data = extract.to_dict()
        elif hasattr(extract, "__dict__"):
            data = extract.__dict__
        self._staging_buffer.append({
            "type": "conversation",
            "data": data,
            "ts": time.time(),
        })
        self._trim_staging_buffer()

    def ingest_document(self, extract: Any) -> None:
        """Add a DocumentExtract to staging."""
        data = extract
        if hasattr(extract, "to_dict"):
            data = extract.to_dict()
        elif hasattr(extract, "__dict__"):
            data = extract.__dict__
        self._staging_buffer.append({
            "type": "document",
            "data": data,
            "ts": time.time(),
        })
        self._trim_staging_buffer()

    def ingest_git_diff(self, project: str, branch: str, summary: str) -> None:
        """Add git context to staging."""
        self._staging_buffer.append({
            "type": "git",
            "data": {"project": project, "branch": branch, "summary": summary},
            "ts": time.time(),
        })
        self._trim_staging_buffer()

    def get_primary_workstream(self) -> Workstream | None:
        """Get the highest-scored active workstream."""
        active = [w for w in self.workstreams if w.status == "active"]
        if not active:
            return None
        return max(active, key=lambda w: w.activity_score)

    def get_active_workstreams(self) -> list[Workstream]:
        """Get all active workstreams sorted by score."""
        return sorted(
            [w for w in self.workstreams if w.status in ("active", "paused")],
            key=lambda w: w.activity_score,
            reverse=True,
        )

    def get_staging_buffer(self) -> list[dict]:
        """Get accumulated signals for LLM processing."""
        return list(self._staging_buffer)

    def clear_staging_buffer(self) -> None:
        """Clear after LLM has processed."""
        self._staging_buffer.clear()

    def apply_llm_results(self, results: list[dict]) -> None:
        """Apply workstream updates from LLM engine.

        Each result dict has:
        - maps_to: existing workstream ID or "new"
        - goal, state, key_people, key_decisions, persona, confidence
        - artifacts, research, communications
        - event_ids, workflow_ids, git_branches, projects, tools_used
        """
        now = time.time()
        for result in results:
            maps_to = result.get("maps_to", "new")
            ws = None

            if maps_to != "new":
                # Find existing workstream
                for w in self.workstreams:
                    if w.id == maps_to:
                        ws = w
                        break

            if ws is None:
                # Create new workstream
                ws = Workstream(
                    inferred_goal=result.get("goal", ""),
                    persona=result.get("persona", "general"),
                    confidence=result.get("confidence", 0.5),
                    created_ts=now,
                    updated_ts=now,
                    last_llm_refresh_ts=now,
                )
                self.workstreams.append(ws)
                logger.info("Created workstream %s: %s", ws.id, ws.inferred_goal)
            else:
                # Update existing
                ws.updated_ts = now
                ws.last_llm_refresh_ts = now

            # Update goal if provided
            goal = result.get("goal")
            if goal:
                ws.inferred_goal = goal

            # Update state
            state = result.get("state")
            if state:
                ws.current_state = state

            # Update persona
            persona = result.get("persona")
            if persona:
                ws.persona = persona

            # Update confidence
            confidence = result.get("confidence")
            if confidence is not None:
                ws.confidence = max(0.0, min(1.0, float(confidence)))

            # Merge key_people
            for person in result.get("key_people", []):
                if person and person not in ws.key_people:
                    ws.key_people.append(person)
            if len(ws.key_people) > 20:
                ws.key_people = ws.key_people[-20:]

            # Merge key_decisions
            for decision in result.get("key_decisions", []):
                if decision and decision not in ws.key_decisions:
                    ws.key_decisions.append(decision)
            if len(ws.key_decisions) > 20:
                ws.key_decisions = ws.key_decisions[-20:]

            # Merge artifacts
            for artifact in result.get("artifacts", []):
                if artifact and artifact not in ws.primary_artifacts:
                    ws.primary_artifacts.append(artifact)
            if len(ws.primary_artifacts) > 30:
                ws.primary_artifacts = ws.primary_artifacts[-30:]

            # Merge research
            for item in result.get("research", []):
                if isinstance(item, dict):
                    ws.supporting_research.append(item)
                elif isinstance(item, str):
                    ws.supporting_research.append({"topic": item})
            if len(ws.supporting_research) > 30:
                ws.supporting_research = ws.supporting_research[-30:]

            # Merge communications
            for item in result.get("communications", []):
                if isinstance(item, dict):
                    ws.related_communications.append(item)
                elif isinstance(item, str):
                    ws.related_communications.append({"summary": item})
            if len(ws.related_communications) > 30:
                ws.related_communications = ws.related_communications[-30:]

            # Merge event_ids
            for eid in result.get("event_ids", []):
                if eid not in ws.event_ids:
                    ws.event_ids.append(eid)
            if len(ws.event_ids) > 500:
                ws.event_ids = ws.event_ids[-500:]

            # Merge workflow_ids
            for wid in result.get("workflow_ids", []):
                if wid not in ws.workflow_ids:
                    ws.workflow_ids.append(wid)

            # Merge git_branches
            for branch in result.get("git_branches", []):
                if branch and branch not in ws.git_branches:
                    ws.git_branches.append(branch)

            # Merge projects
            for project in result.get("projects", []):
                if project and project not in ws.projects:
                    ws.projects.append(project)

            # Merge tools_used
            for tool in result.get("tools_used", []):
                if tool and tool not in ws.tools_used:
                    ws.tools_used.append(tool)
            if len(ws.tools_used) > 20:
                ws.tools_used = ws.tools_used[-20:]

            # Boost score for fresh LLM update
            ws.boost(0.3)
            ws.event_count += len(result.get("event_ids", []))

        # Enforce MAX_ACTIVE — archive lowest-scored beyond limit
        active = [w for w in self.workstreams if w.status == "active"]
        if len(active) > self.MAX_ACTIVE:
            active.sort(key=lambda w: w.activity_score, reverse=True)
            for ws in active[self.MAX_ACTIVE:]:
                ws.status = "paused"

        self.save_to_db()

    def _maybe_decay(self) -> None:
        """Periodically decay workstream scores and update statuses."""
        now = time.time()
        if now - self._last_decay_ts < self.DECAY_INTERVAL:
            return
        self._last_decay_ts = now
        for ws in self.workstreams:
            ws.decay(now)
            elapsed = now - ws.updated_ts
            if ws.status == "active" and elapsed > self.PAUSE_THRESHOLD:
                ws.status = "paused"
            elif ws.status == "paused" and elapsed > self.STALE_THRESHOLD:
                ws.status = "stale"

    def _trim_staging_buffer(self) -> None:
        """Keep staging buffer bounded."""
        if len(self._staging_buffer) > MAX_STAGING_BUFFER:
            self._staging_buffer = self._staging_buffer[-MAX_STAGING_BUFFER:]

    def _load_from_db(self) -> None:
        """Load active workstreams from database."""
        try:
            from ..store.workstream_store import load_active_workstreams
            rows = load_active_workstreams(self._db)
            for row in rows:
                ws = Workstream(
                    id=row["id"],
                    inferred_goal=row.get("inferred_goal", ""),
                    status=row.get("status", "active"),
                    persona=row.get("persona", "general"),
                    created_ts=row.get("created_ts", 0),
                    updated_ts=row.get("updated_ts", 0),
                    last_llm_refresh_ts=row.get("last_llm_refresh_ts", 0),
                    confidence=row.get("confidence", 0.5),
                    primary_artifacts=row.get("primary_artifacts", []),
                    supporting_research=row.get("supporting_research", []),
                    related_communications=row.get("related_communications", []),
                    key_decisions=row.get("key_decisions", []),
                    current_state=row.get("current_state", ""),
                    key_people=row.get("key_people", []),
                    event_ids=row.get("event_ids", []),
                    git_branches=row.get("git_branches", []),
                    projects=row.get("projects", []),
                    tools_used=row.get("tools_used", []),
                    activity_score=row.get("activity_score", 1.0),
                    event_count=row.get("event_count", 0),
                )
                self.workstreams.append(ws)
            if self.workstreams:
                logger.info("Loaded %d workstreams from DB", len(self.workstreams))
        except Exception:
            logger.debug("Could not load workstreams from DB (table may not exist yet)")

    def save_to_db(self) -> None:
        """Persist current workstreams to database."""
        if not self._db:
            return
        try:
            from ..store.workstream_store import save_workstream
            for ws in self.workstreams:
                save_workstream(self._db, ws)
        except Exception:
            logger.debug("Could not save workstreams to DB", exc_info=True)

    def to_dict(self) -> dict:
        """Serialize for API output."""
        primary = self.get_primary_workstream()
        return {
            "workstreams": [w.to_dict() for w in self.get_active_workstreams()],
            "primary": primary.to_dict() if primary else None,
            "staging_buffer_size": len(self._staging_buffer),
        }
