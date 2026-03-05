"""Parser for video conferencing apps."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class ZoomParser(AppParser):
    name = "zoom"
    app_names = ["zoom.us", "Zoom", "Google Meet", "FaceTime"]
    bundle_ids = [
        "us.zoom.xos",
        "com.apple.FaceTime",
    ]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="meeting",
            sub_activity="video_call",
        )

        if not title:
            return ctx

        # Don't capture meeting titles (potentially sensitive)
        # Just note that user is in a meeting
        lower = title.lower()
        if "screen share" in lower or "sharing" in lower:
            ctx.sub_activity = "screen_sharing"

        return ctx
