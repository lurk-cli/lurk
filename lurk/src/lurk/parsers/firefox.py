"""Parser for Firefox browser."""

from __future__ import annotations

from .base import AppParser, ParsedContext
from .chrome import ChromeParser


class FirefoxParser(AppParser):
    name = "firefox"
    app_names = ["Firefox"]
    bundle_ids = ["org.mozilla.firefox"]

    _chrome = ChromeParser()

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        # Firefox: "Page Title — Mozilla Firefox"
        suffix = " — Mozilla Firefox"
        if title.endswith(suffix):
            title = title[: -len(suffix)]

        ctx = self._chrome.parse(title, app, bundle_id)
        ctx.app = app
        ctx.parser_name = self.name
        return ctx
