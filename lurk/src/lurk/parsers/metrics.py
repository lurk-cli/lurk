"""Parser metrics — tracks success/failure rates per parser for degradation detection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("lurk.parsers")


@dataclass
class ParserStats:
    """Cumulative stats for a single parser."""

    calls: int = 0
    successes: int = 0
    empty_returns: int = 0
    errors: int = 0

    @property
    def success_rate(self) -> float:
        if self.calls == 0:
            return 1.0
        return self.successes / self.calls


class ParserMetrics:
    """Aggregates parser stats and warns on degradation."""

    def __init__(self) -> None:
        self._stats: dict[str, ParserStats] = {}
        self._warn_threshold = 0.5
        self._min_calls_for_warn = 10

    def _get(self, parser_name: str) -> ParserStats:
        if parser_name not in self._stats:
            self._stats[parser_name] = ParserStats()
        return self._stats[parser_name]

    def record_call(self, parser_name: str) -> None:
        self._get(parser_name).calls += 1

    def record_success(self, parser_name: str) -> None:
        self._get(parser_name).successes += 1

    def record_empty(self, parser_name: str) -> None:
        stats = self._get(parser_name)
        stats.empty_returns += 1
        self._check_degradation(parser_name, stats)

    def record_error(self, parser_name: str) -> None:
        stats = self._get(parser_name)
        stats.errors += 1
        self._check_degradation(parser_name, stats)

    def _check_degradation(self, parser_name: str, stats: ParserStats) -> None:
        if stats.calls >= self._min_calls_for_warn:
            if stats.success_rate < self._warn_threshold:
                logger.warning(
                    "Parser '%s' degraded: %.0f%% success rate (%d/%d calls)",
                    parser_name,
                    stats.success_rate * 100,
                    stats.successes,
                    stats.calls,
                )

    def summary(self) -> dict[str, dict[str, float | int]]:
        """Return a summary dict of all parser stats."""
        return {
            name: {
                "calls": s.calls,
                "successes": s.successes,
                "empty_returns": s.empty_returns,
                "errors": s.errors,
                "success_rate": round(s.success_rate, 3),
            }
            for name, s in self._stats.items()
            if s.calls > 0
        }
