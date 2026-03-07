"""SQLite database access for the Python intelligence layer."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .schema import (
    ARTIFACTS_INDEXES,
    ARTIFACTS_SCHEMA,
    CODE_SNAPSHOTS_INDEXES,
    CODE_SNAPSHOTS_SCHEMA,
    CAPTURES_FTS,
    CAPTURES_INDEXES,
    CAPTURES_SCHEMA,
    DECISIONS_INDEXES,
    DECISIONS_SCHEMA,
    ENRICHED_EVENTS_INDEXES,
    ENRICHED_EVENTS_SCHEMA,
    RAW_EVENTS_INDEXES,
    RAW_EVENTS_SCHEMA,
    SESSIONS_INDEXES,
    SESSIONS_SCHEMA,
    STAKEHOLDERS_INDEXES,
    STAKEHOLDERS_SCHEMA,
    WORKFLOWS_INDEXES,
    WORKFLOWS_SCHEMA,
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
    conn.execute(CAPTURES_SCHEMA)
    for idx in CAPTURES_INDEXES:
        conn.execute(idx)
    try:
        conn.execute(CAPTURES_FTS)
    except Exception:
        pass  # FTS may already exist
    conn.execute(WORKFLOWS_SCHEMA)
    for idx in WORKFLOWS_INDEXES:
        conn.execute(idx)
    conn.execute(CODE_SNAPSHOTS_SCHEMA)
    for idx in CODE_SNAPSHOTS_INDEXES:
        conn.execute(idx)
    conn.execute(STAKEHOLDERS_SCHEMA)
    for idx in STAKEHOLDERS_INDEXES:
        conn.execute(idx)
    conn.execute(ARTIFACTS_SCHEMA)
    for idx in ARTIFACTS_INDEXES:
        conn.execute(idx)
    conn.execute(DECISIONS_SCHEMA)
    for idx in DECISIONS_INDEXES:
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


def insert_capture(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    """Insert a viewport/typing capture into the knowledge trail."""
    headers = data.get("headers")
    if isinstance(headers, list):
        headers = json.dumps(headers)
    meta = data.get("meta")
    if isinstance(meta, dict):
        meta = json.dumps(meta)
    topics = data.get("topics")
    if isinstance(topics, list):
        topics = json.dumps(topics)

    cursor = conn.execute(
        """INSERT INTO captures
        (ts, source, capture_type, app, hostname, url, page_title, headers,
         meta, viewport_text, page_content, typing_text, dwell_seconds,
         scroll_depth, engagement_score, topics, workflow_id, summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("timestamp", time.time()),
            data.get("source", "viewport"),
            data.get("type", "unknown"),
            data.get("app"),
            data.get("hostname"),
            data.get("url"),
            data.get("page_title"),
            headers,
            meta,
            data.get("viewport_text"),
            data.get("page_content"),
            data.get("typing_text") or data.get("text_preview"),
            data.get("dwell_seconds", 0),
            data.get("scroll_depth", 0),
            data.get("engagement_score", 0),
            topics,
            data.get("workflow_id"),
            data.get("summary"),
        ),
    )
    row_id = cursor.lastrowid or 0

    # Update FTS index
    try:
        conn.execute(
            """INSERT INTO captures_fts (rowid, page_title, headers, viewport_text,
               page_content, typing_text, topics, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row_id,
                data.get("page_title"),
                headers if isinstance(headers, str) else json.dumps(headers) if headers else None,
                data.get("viewport_text"),
                data.get("page_content"),
                data.get("typing_text") or data.get("text_preview"),
                topics if isinstance(topics, str) else json.dumps(topics) if topics else None,
                data.get("summary"),
            ),
        )
    except Exception:
        pass  # FTS update is best-effort

    conn.commit()
    return row_id


def fetch_captures_for_workflow(
    conn: sqlite3.Connection, workflow_id: int, limit: int = 20
) -> list[dict[str, Any]]:
    """Fetch captures belonging to a workflow, ordered by relevance."""
    cursor = conn.execute(
        """SELECT * FROM captures
        WHERE workflow_id = ?
        ORDER BY engagement_score DESC, ts DESC
        LIMIT ?""",
        (workflow_id, limit),
    )
    results = []
    for row in cursor.fetchall():
        d = dict(row)
        for field in ("headers", "meta", "topics"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        results.append(d)
    return results


def fetch_recent_captures(
    conn: sqlite3.Connection, hours: float = 2, limit: int = 50
) -> list[dict[str, Any]]:
    """Fetch recent captures regardless of workflow."""
    import time as _time
    since = _time.time() - hours * 3600
    cursor = conn.execute(
        """SELECT * FROM captures
        WHERE ts > ?
        ORDER BY engagement_score DESC, ts DESC
        LIMIT ?""",
        (since, limit),
    )
    results = []
    for row in cursor.fetchall():
        d = dict(row)
        for field in ("headers", "meta", "topics"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        results.append(d)
    return results


def search_captures(
    conn: sqlite3.Connection, query: str, limit: int = 20
) -> list[dict[str, Any]]:
    """Full-text search across captures."""
    cursor = conn.execute(
        """SELECT captures.* FROM captures_fts
        JOIN captures ON captures.id = captures_fts.rowid
        WHERE captures_fts MATCH ?
        ORDER BY rank
        LIMIT ?""",
        (query, limit),
    )
    results = []
    for row in cursor.fetchall():
        d = dict(row)
        for field in ("headers", "meta", "topics"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        results.append(d)
    return results


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


def insert_code_snapshot(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    """Insert a code snapshot — the actual diff content of what agents wrote."""
    files_touched = data.get("files_touched") or data.get("files", [])
    if isinstance(files_touched, list) and files_touched and isinstance(files_touched[0], dict):
        files_touched = [f.get("path", "") for f in files_touched]

    file_diffs = data.get("file_diffs") or data.get("files", [])
    if isinstance(file_diffs, list) and file_diffs:
        if hasattr(file_diffs[0], "to_dict"):
            file_diffs = [fd.to_dict() for fd in file_diffs]

    cursor = conn.execute(
        """INSERT INTO code_snapshots
        (ts, project, repo_path, branch, change_type, commit_hash,
         files_touched, file_diffs, full_diff, summary,
         total_additions, total_deletions, workflow_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("timestamp", time.time()),
            data.get("project", ""),
            data.get("repo_path"),
            data.get("branch"),
            data.get("change_type", "working"),
            data.get("commit_hash"),
            json.dumps(files_touched),
            json.dumps(file_diffs),
            data.get("full_diff", ""),
            data.get("summary", ""),
            data.get("total_additions", 0),
            data.get("total_deletions", 0),
            data.get("workflow_id"),
        ),
    )
    conn.commit()
    return cursor.lastrowid or 0


def fetch_recent_code_snapshots(
    conn: sqlite3.Connection, project: str | None = None,
    hours: float = 4, limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch recent code snapshots with actual diff content."""
    since = time.time() - hours * 3600
    if project:
        cursor = conn.execute(
            """SELECT * FROM code_snapshots
            WHERE ts > ? AND project = ?
            ORDER BY ts DESC LIMIT ?""",
            (since, project, limit),
        )
    else:
        cursor = conn.execute(
            """SELECT * FROM code_snapshots
            WHERE ts > ? ORDER BY ts DESC LIMIT ?""",
            (since, limit),
        )
    results = []
    for row in cursor.fetchall():
        d = dict(row)
        for field in ("files_touched", "file_diffs"):
            if d.get(field):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        results.append(d)
    return results
