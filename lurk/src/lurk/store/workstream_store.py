"""Database persistence for workstreams."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger("lurk.store.workstreams")

WORKSTREAMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS workstreams (
    id TEXT PRIMARY KEY,
    inferred_goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    persona TEXT NOT NULL DEFAULT 'general',
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    last_llm_refresh_ts REAL NOT NULL DEFAULT 0,
    confidence REAL DEFAULT 0.5,
    primary_artifacts TEXT,
    supporting_research TEXT,
    related_communications TEXT,
    key_decisions TEXT,
    current_state TEXT,
    key_people TEXT,
    git_branches TEXT,
    projects TEXT,
    tools_used TEXT,
    activity_score REAL DEFAULT 1.0,
    event_count INTEGER DEFAULT 0
)
"""

WORKSTREAMS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_workstreams_status ON workstreams(status, updated_ts)",
]

WORKSTREAM_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS workstream_events (
    workstream_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (workstream_id, event_id)
)
"""

WORKSTREAM_EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_workstream_events_ws ON workstream_events(workstream_id)",
]


def save_workstream(db_path: str | sqlite3.Connection, workstream: Any) -> None:
    """Save a workstream to the database.

    db_path can be a path string or an existing sqlite3.Connection.
    workstream is a Workstream dataclass instance.
    """
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO workstreams
            (id, inferred_goal, status, persona, created_ts, updated_ts,
             last_llm_refresh_ts, confidence, primary_artifacts,
             supporting_research, related_communications, key_decisions,
             current_state, key_people, git_branches, projects, tools_used,
             activity_score, event_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                workstream.id,
                workstream.inferred_goal,
                workstream.status,
                workstream.persona,
                workstream.created_ts,
                workstream.updated_ts,
                workstream.last_llm_refresh_ts,
                workstream.confidence,
                json.dumps(workstream.primary_artifacts),
                json.dumps(workstream.supporting_research),
                json.dumps(workstream.related_communications),
                json.dumps(workstream.key_decisions),
                workstream.current_state,
                json.dumps(workstream.key_people),
                json.dumps(workstream.git_branches),
                json.dumps(workstream.projects),
                json.dumps(workstream.tools_used),
                workstream.activity_score,
                workstream.event_count,
            ),
        )

        # Sync event associations
        if workstream.event_ids:
            now = time.time()
            for event_id in workstream.event_ids:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO workstream_events
                        (workstream_id, event_id, ts)
                        VALUES (?, ?, ?)""",
                        (workstream.id, event_id, now),
                    )
                except Exception:
                    pass  # Best effort

        conn.commit()
    except Exception:
        logger.debug("Failed to save workstream %s", workstream.id, exc_info=True)


def load_active_workstreams(db_path: str | sqlite3.Connection) -> list[dict]:
    """Load active and paused workstreams from the database."""
    conn = _get_conn(db_path)
    try:
        cursor = conn.execute(
            """SELECT * FROM workstreams
            WHERE status IN ('active', 'paused')
            ORDER BY updated_ts DESC
            LIMIT 20"""
        )
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            # Parse JSON fields
            for field in (
                "primary_artifacts", "supporting_research",
                "related_communications", "key_decisions", "key_people",
                "git_branches", "projects", "tools_used",
            ):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        d[field] = []
                else:
                    d[field] = []

            # Load associated event_ids
            try:
                ev_cursor = conn.execute(
                    "SELECT event_id FROM workstream_events WHERE workstream_id = ?",
                    (d["id"],),
                )
                d["event_ids"] = [r["event_id"] for r in ev_cursor.fetchall()]
            except Exception:
                d["event_ids"] = []

            results.append(d)
        return results
    except Exception:
        logger.debug("Could not load workstreams (table may not exist)", exc_info=True)
        return []


def delete_stale_workstreams(db_path: str | sqlite3.Connection, older_than: float) -> None:
    """Delete workstreams that have been stale for longer than older_than seconds."""
    conn = _get_conn(db_path)
    cutoff = time.time() - older_than
    try:
        # Get IDs to delete
        cursor = conn.execute(
            "SELECT id FROM workstreams WHERE status = 'stale' AND updated_ts < ?",
            (cutoff,),
        )
        ids = [row["id"] for row in cursor.fetchall()]
        if not ids:
            return

        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"DELETE FROM workstream_events WHERE workstream_id IN ({placeholders})",
            ids,
        )
        conn.execute(
            f"DELETE FROM workstreams WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        logger.info("Deleted %d stale workstreams", len(ids))
    except Exception:
        logger.debug("Could not delete stale workstreams", exc_info=True)


def _get_conn(db_path: str | sqlite3.Connection) -> sqlite3.Connection:
    """Get a connection — accepts either a path or existing connection."""
    if isinstance(db_path, sqlite3.Connection):
        return db_path
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn
