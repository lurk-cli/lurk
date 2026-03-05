"""Parser for Discord."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class DiscordParser(AppParser):
    name = "discord"
    app_names = ["Discord"]
    bundle_ids = ["com.hnc.Discord"]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="communicating",
            sub_activity="messaging",
        )

        if not title or title == "Discord":
            return ctx

        # Discord title: "#channel-name - Server Name - Discord"
        cleaned = title
        if cleaned.endswith(" - Discord"):
            cleaned = cleaned[:-10]

        parts = [p.strip() for p in cleaned.split(" - ")]
        if len(parts) >= 2:
            ctx.channel = parts[0]
            ctx.project = parts[1]  # Server name
        elif parts:
            ctx.channel = parts[0]

        return ctx
