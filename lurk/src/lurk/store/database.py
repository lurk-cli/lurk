"""SQLite database access for the Python intelligence layer."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from .schema import (
    ENRICHED_EVENTS_INDEXES,
    ENRICHED_EVENTS_SCHEMA,
    RAW_EVENTS_INDEXES,
    RAW_EVENTS_SCHEMA,
    SESSIONS_INDEXES,
    SESSIONS_SCHEMA,
)

DB_PATH = Path.home() / ".lurk" / "store.db"


def get_connection() -> sqlite3.Connection:
    """Get a connection to the shared SQLite database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist."""
    conn.execute(RAW_EVENTS_SCHEMA)
    for idx in RAW_EVENTS_INDEXES:
        conn.execute(idx)
    conn.execute(ENRICHED_EVENTS_SCHEMA)
    for idx in ENRICHED_EVENTS_INDEXES:
        conn.execute(idx)
    conn.execute(SESSIONS_SCHEMA)
    for idx in SESSIONS_INDEXES:
        conn.execute(idx)
    _migrate_agent_columns(conn)
    conn.commit()


def fetch_unenriched_events(
    conn: sqlite3.Connection, limit: int = 100
) -> list[dict[str, Any]]:
    """Fetch raw events that haven't been enriched yet."""
    cursor = conn.execute(
        "SELECT id, ts, event_type, app, bundle_id, title, data "
        "FROM events WHERE enriched = 0 ORDER BY ts ASC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d["data"]:
            try:
                d["data"] = json.loads(d["data"])
            except (json.JSONDecodeError, TypeError):
                pass
        result.append(d)
    return result


def mark_enriched(conn: sqlite3.Connection, event_ids: list[int]) -> None:
    """Mark events as enriched."""
    if not event_ids:
        return
    placeholders = ",".join("?" for _ in event_ids)
    conn.execute(
        f"UPDATE events SET enriched = 1 WHERE id IN ({placeholders})", event_ids
    )
    conn.commit()


def insert_enriched_event(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    """Insert an enriched event."""
    conn.execute(
        """INSERT INTO enriched_events
        (event_id, ts, app, title, file, project, language, ticket, branch,
         url_domain, topic, channel, document_name, activity, sub_activity,
         intent, interruptibility, agent_tool, agent_state, data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("event_id"),
            data.get("ts"),
            data.get("app"),
            data.get("title"),
            data.get("file"),
            data.get("project"),
            data.get("language"),
            data.get("ticket"),
            data.get("branch"),
            data.get("url_domain"),
            data.get("topic"),
            data.get("channel"),
            data.get("document_name"),
            data.get("activity"),
            data.get("sub_activity"),
            data.get("intent"),
            data.get("interruptibility"),
            data.get("agent_tool"),
            data.get("agent_state"),
            json.dumps(data) if data else None,
        ),
    )


def _migrate_agent_columns(conn: sqlite3.Connection) -> None:
    """Add agent_tool/agent_state columns to existing enriched_events tables."""
    try:
        cursor = conn.execute("PRAGMA table_info(enriched_events)")
        columns = {row[1] for row in cursor.fetchall()}
        if "agent_tool" not in columns:
            conn.execute("ALTER TABLE enriched_events ADD COLUMN agent_tool TEXT")
        if "agent_state" not in columns:
            conn.execute("ALTER TABLE enriched_events ADD COLUMN agent_state TEXT")
    except Exception:
        pass  # Table may not exist yet; CREATE TABLE will handle it


def fetch_recent_enriched(
    conn: sqlite3.Connection, hours: float = 24, limit: int = 500
) -> list[dict[str, Any]]:
    """Fetch recent enriched events."""
    import time

    since = time.time() - (hours * 3600)
    cursor = conn.execute(
        "SELECT * FROM enriched_events WHERE ts > ? ORDER BY ts DESC LIMIT ?",
        (since, limit),
    )
    return [dict(row) for row in cursor.fetchall()]


def save_session(conn: sqlite3.Connection, session_data: dict[str, Any]) -> int:
    """Save a completed session to the database. Returns the session ID."""
    cursor = conn.execute(
        """INSERT INTO sessions
        (start_ts, end_ts, duration_seconds, projects, files_edited,
         tickets, tools, context_switches, focus_blocks_count, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_data.get("start_ts"),
            session_data.get("end_ts"),
            session_data.get("duration_seconds"),
            json.dumps(session_data.get("projects", [])),
            json.dumps(session_data.get("files_edited", [])),
            json.dumps(session_data.get("tickets", [])),
            json.dumps(session_data.get("tools", [])),
            session_data.get("context_switches", 0),
            session_data.get("focus_blocks_count", 0),
            session_data.get("summary"),
        ),
    )
    conn.commit()
    return cursor.lastrowid or 0


def fetch_recent_sessions(
    conn: sqlite3.Connection, days: int = 7, limit: int = 50
) -> list[dict[str, Any]]:
    """Fetch recent completed sessions for cross-session memory."""
    import time

    since = time.time() - (days * 86400)
    cursor = conn.execute(
        "SELECT * FROM sessions WHERE start_ts > ? ORDER BY start_ts DESC LIMIT ?",
        (since, limit),
    )
    sessions = []
    for row in cursor.fetchall():
        d = dict(row)
        # Parse JSON fields
        for field in ("projects", "files_edited", "tickets", "tools"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    d[field] = []
            else:
                d[field] = []
        sessions.append(d)
    return sessions


def fetch_recent_raw_events(
    conn: sqlite3.Connection, hours: float = 1, limit: int = 200
) -> list[dict[str, Any]]:
    """Fetch recent raw events for log display."""
    import time

    since = time.time() - (hours * 3600)
    cursor = conn.execute(
        "SELECT * FROM events WHERE ts > ? ORDER BY ts DESC LIMIT ?",
        (since, limit),
    )
    rows = []
    for row in cursor.fetchall():
        d = dict(row)
        if d.get("data"):
            try:
                d["data"] = json.loads(d["data"])
            except (json.JSONDecodeError, TypeError):
                pass
        rows.append(d)
    return rows
