"""Parser for Arc browser."""

from __future__ import annotations

from .base import AppParser, ParsedContext
from .chrome import ChromeParser


class ArcParser(AppParser):
    name = "arc"
    app_names = ["Arc"]
    bundle_ids = ["company.thebrowser.Browser"]

    _chrome = ChromeParser()

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = self._chrome.parse(title, app, bundle_id)
        ctx.app = app
        ctx.parser_name = self.name
        return ctx
