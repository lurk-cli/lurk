"""Parser for Notion."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class NotionParser(AppParser):
    name = "notion"
    app_names = ["Notion"]
    bundle_ids = ["notion.id"]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="writing",
            sub_activity="notes",
        )

        if not title or title == "Notion":
            return ctx

        # Notion title: "Page Title — Notion"
        cleaned = title
        for suffix in [" — Notion", " - Notion"]:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break

        ctx.document_name = cleaned

        return ctx
