"""Parser for Xcode."""

from __future__ import annotations

from .base import AppParser, ParsedContext, language_from_filename, detect_file_role


class XcodeParser(AppParser):
    name = "xcode"
    app_names = ["Xcode"]
    bundle_ids = ["com.apple.dt.Xcode"]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="coding",
        )

        if not title or title == "Xcode":
            return ctx

        # Xcode title formats:
        # "filename.swift — ProjectName — Xcode"
        # "ProjectName — Xcode"
        parts = [p.strip() for p in title.split(" — ")]

        if len(parts) >= 3:
            ctx.file = parts[0]
            ctx.project = parts[1]
            ctx.language = language_from_filename(parts[0])
            role = detect_file_role(parts[0])
            if role == "testing":
                ctx.sub_activity = "testing"
        elif len(parts) == 2:
            # Could be "File — Xcode" or "Project — Xcode"
            if "." in parts[0]:
                ctx.file = parts[0]
                ctx.language = language_from_filename(parts[0])
            else:
                ctx.project = parts[0]

        return ctx
