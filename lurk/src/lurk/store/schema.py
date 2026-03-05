"""SQLite schema for raw and enriched events tables."""

# Raw events table (normally created by Swift daemon, but needed for standalone Python)
RAW_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    app TEXT,
    bundle_id TEXT,
    title TEXT,
    data TEXT,
    enriched INTEGER DEFAULT 0
)
"""

RAW_EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_events_unenriched ON events(enriched) WHERE enriched = 0",
]

ENRICHED_EVENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS enriched_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER REFERENCES events(id),
    ts REAL NOT NULL,
    app TEXT,
    title TEXT,
    file TEXT,
    project TEXT,
    language TEXT,
    ticket TEXT,
    branch TEXT,
    url_domain TEXT,
    topic TEXT,
    channel TEXT,
    document_name TEXT,
    activity TEXT,
    sub_activity TEXT,
    intent TEXT,
    interruptibility TEXT,
    agent_tool TEXT,
    agent_state TEXT,
    data TEXT
)
"""

ENRICHED_EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_enriched_ts ON enriched_events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_enriched_project ON enriched_events(project)",
]

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts REAL NOT NULL,
    end_ts REAL,
    duration_seconds REAL,
    projects TEXT,
    files_edited TEXT,
    tickets TEXT,
    tools TEXT,
    context_switches INTEGER DEFAULT 0,
    focus_blocks_count INTEGER DEFAULT 0,
    summary TEXT
)
"""

SESSIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions(start_ts)",
]
