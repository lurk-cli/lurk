"""Parser for Microsoft Teams."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class TeamsParser(AppParser):
    name = "teams"
    app_names = ["Microsoft Teams"]
    bundle_ids = ["com.microsoft.teams", "com.microsoft.teams2"]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="communicating",
        )

        if not title:
            return ctx

        lower = title.lower()

        if "meeting" in lower or "call" in lower:
            ctx.activity = "meeting"
            ctx.sub_activity = "video_call"
        elif "chat" in lower:
            ctx.sub_activity = "messaging"
        else:
            ctx.sub_activity = "messaging"

        # Teams title: "Channel | Team — Microsoft Teams"
        cleaned = title
        for suffix in [" — Microsoft Teams", " - Microsoft Teams"]:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break

        parts = cleaned.split(" | ")
        if len(parts) >= 2:
            ctx.channel = parts[0].strip()
            ctx.project = parts[1].strip()
        elif parts:
            ctx.channel = parts[0].strip()

        return ctx
