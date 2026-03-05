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

# Knowledge trail — viewport captures, typing, page content
CAPTURES_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    capture_type TEXT NOT NULL,
    app TEXT,
    hostname TEXT,
    url TEXT,
    page_title TEXT,
    headers TEXT,
    meta TEXT,
    viewport_text TEXT,
    page_content TEXT,
    typing_text TEXT,
    dwell_seconds REAL DEFAULT 0,
    scroll_depth INTEGER DEFAULT 0,
    engagement_score REAL DEFAULT 0,
    topics TEXT,
    workflow_id INTEGER,
    summary TEXT
)
"""

CAPTURES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_captures_ts ON captures(ts)",
    "CREATE INDEX IF NOT EXISTS idx_captures_workflow ON captures(workflow_id)",
]

# FTS index for full-text search across captures
CAPTURES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS captures_fts USING fts5(
    page_title,
    headers,
    viewport_text,
    page_content,
    typing_text,
    topics,
    summary,
    content=captures,
    content_rowid=id
)
"""

# Workflow clusters — groups of related captures
WORKFLOWS_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts REAL NOT NULL,
    updated_ts REAL NOT NULL,
    topic_keywords TEXT NOT NULL,
    label TEXT,
    capture_count INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1
)
"""

WORKFLOWS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_workflows_active ON workflows(is_active, updated_ts)",
]

# Code snapshots — the actual code that coding agents write
CODE_SNAPSHOTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    project TEXT NOT NULL,
    repo_path TEXT,
    branch TEXT,
    change_type TEXT NOT NULL,
    commit_hash TEXT,
    files_touched TEXT,
    file_diffs TEXT,
    full_diff TEXT,
    summary TEXT,
    total_additions INTEGER DEFAULT 0,
    total_deletions INTEGER DEFAULT 0,
    workflow_id INTEGER
)
"""

CODE_SNAPSHOTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_code_snapshots_ts ON code_snapshots(ts)",
    "CREATE INDEX IF NOT EXISTS idx_code_snapshots_project ON code_snapshots(project)",
    "CREATE INDEX IF NOT EXISTS idx_code_snapshots_workflow ON code_snapshots(workflow_id)",
]
