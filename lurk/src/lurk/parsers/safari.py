"""Parser for Safari browser."""

from __future__ import annotations

import re

from .base import AppParser, ParsedContext
from .chrome import ChromeParser


class SafariParser(AppParser):
    name = "safari"
    app_names = ["Safari"]
    bundle_ids = ["com.apple.Safari"]

    # Reuse Chrome's classification logic — titles are similar
    _chrome = ChromeParser()

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        # Safari titles don't include " — Safari" suffix typically
        # but the page title format is the same as Chrome
        ctx = self._chrome.parse(title, app, bundle_id)
        ctx.app = app
        ctx.parser_name = self.name
        return ctx
