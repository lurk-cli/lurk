"""Exclusion rules engine — filters events that should never be captured.

Supports:
- App name exclusions (exact match, case-insensitive)
- Bundle ID exclusions (exact match)
- Title pattern exclusions (glob-style wildcards)
- Time block exclusions (hourly ranges)
"""

from __future__ import annotations

import fnmatch
import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import ExclusionConfig

logger = logging.getLogger("lurk.config")


class ExclusionEngine:
    """Evaluates events against user-defined exclusion rules."""

    def __init__(self, config: ExclusionConfig) -> None:
        self.apps = {a.lower() for a in config.apps}
        self.bundle_ids = set(config.bundle_ids)
        self.title_patterns = config.title_patterns
        self.time_blocks = config.time_blocks

    def should_exclude(
        self,
        app: str | None = None,
        bundle_id: str | None = None,
        title: str | None = None,
        timestamp: float | None = None,
    ) -> bool:
        """Check if an event should be excluded.

        Returns True if ANY exclusion rule matches.
        """
        # App name check
        if app and app.lower() in self.apps:
            logger.debug("Excluded by app: %s", app)
            return True

        # Bundle ID check
        if bundle_id and bundle_id in self.bundle_ids:
            logger.debug("Excluded by bundle_id: %s", bundle_id)
            return True

        # Title pattern check (glob-style)
        if title and self.title_patterns:
            title_lower = title.lower()
            for pattern in self.title_patterns:
                if fnmatch.fnmatch(title_lower, pattern.lower()):
                    logger.debug("Excluded by title pattern: %s", pattern)
                    return True

        # Time block check
        if timestamp and self.time_blocks:
            dt = datetime.fromtimestamp(timestamp)
            current_hour = dt.hour + dt.minute / 60.0
            for block in self.time_blocks:
                start = block.get("start", 0)
                end = block.get("end", 24)
                days = block.get("days", [])

                # Check day of week if specified (0=Monday, 6=Sunday)
                if days and dt.weekday() not in days:
                    continue

                if start <= current_hour < end:
                    logger.debug("Excluded by time block: %s-%s", start, end)
                    return True

        return False

    @property
    def has_rules(self) -> bool:
        """Whether any exclusion rules are configured."""
        return bool(self.apps or self.bundle_ids or self.title_patterns or self.time_blocks)
