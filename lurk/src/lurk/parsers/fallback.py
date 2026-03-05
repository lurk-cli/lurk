"""Fallback parser — matches everything, returns minimal context."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class FallbackParser(AppParser):
    name = "fallback"

    def can_parse(self, app: str, bundle_id: str | None = None) -> bool:
        return True  # Always matches

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        return ParsedContext(
            app=app,
            parser_name=self.name,
            activity="unknown",
        )
