"""Data retention and auto-purge — keeps the database size bounded.

Runs daily to:
- Delete raw events older than retention period
- Delete enriched events older than retention period
- Compact old sessions into summaries
- VACUUM if significant data was deleted
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import RetentionConfig

logger = logging.getLogger("lurk.config")


def run_retention(conn: sqlite3.Connection, config: RetentionConfig) -> dict[str, int]:
    """Run retention policy, deleting old data.

    Returns dict of counts of deleted rows per table.
    """
    now = time.time()
    results = {}

    # Delete old raw events
    raw_cutoff = now - config.raw_events_days * 86400
    cursor = conn.execute("DELETE FROM events WHERE ts < ?", (raw_cutoff,))
    results["events"] = cursor.rowcount
    if cursor.rowcount > 0:
        logger.info("Purged %d raw events older than %d days", cursor.rowcount, config.raw_events_days)

    # Delete old enriched events
    enriched_cutoff = now - config.enriched_events_days * 86400
    try:
        cursor = conn.execute("DELETE FROM enriched_events WHERE ts < ?", (enriched_cutoff,))
        results["enriched_events"] = cursor.rowcount
        if cursor.rowcount > 0:
            logger.info("Purged %d enriched events older than %d days",
                        cursor.rowcount, config.enriched_events_days)
    except sqlite3.OperationalError:
        results["enriched_events"] = 0

    conn.commit()

    # VACUUM if significant data was deleted
    total_deleted = sum(results.values())
    if total_deleted > 1000:
        try:
            conn.execute("VACUUM")
            logger.info("Database vacuumed after deleting %d rows", total_deleted)
        except sqlite3.OperationalError:
            pass

    return results


def get_db_stats(conn: sqlite3.Connection) -> dict[str, int | float]:
    """Get database size and row count statistics."""
    stats: dict[str, int | float] = {}

    try:
        stats["events_count"] = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    except sqlite3.OperationalError:
        stats["events_count"] = 0

    try:
        stats["enriched_count"] = conn.execute("SELECT COUNT(*) FROM enriched_events").fetchone()[0]
    except sqlite3.OperationalError:
        stats["enriched_count"] = 0

    try:
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        stats["db_size_mb"] = round(page_count * page_size / (1024 * 1024), 2)
    except Exception:
        stats["db_size_mb"] = 0.0

    return stats
