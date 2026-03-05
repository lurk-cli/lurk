"""Parser for Slack."""

from __future__ import annotations

import re

from .base import AppParser, ParsedContext


class SlackParser(AppParser):
    name = "slack"
    app_names = ["Slack"]
    bundle_ids = ["com.tinyspeck.slackmacgap"]

    # Title formats:
    # "channel-name - Workspace Name - Slack"
    # "Person Name - Workspace Name - Slack"
    # "Thread in #channel-name - Workspace Name - Slack"

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="communicating",
            sub_activity="messaging",
        )

        if not title:
            return ctx

        # Remove " - Slack" suffix
        cleaned = title
        if cleaned.endswith(" - Slack"):
            cleaned = cleaned[:-8]

        parts = [p.strip() for p in cleaned.split(" - ")]

        if len(parts) >= 2:
            ctx.channel = parts[0]
            ctx.project = parts[1]  # Workspace name
        elif len(parts) == 1:
            ctx.channel = parts[0]

        # Detect thread context
        if ctx.channel and ctx.channel.startswith("Thread in "):
            ctx.channel = ctx.channel[10:]  # Strip "Thread in "
            ctx.sub_activity = "thread"

        # Detect if it's a DM (no # prefix in channel names for DMs)
        if ctx.channel and not ctx.channel.startswith("#"):
            ctx.sub_activity = "direct_message"

        return ctx
