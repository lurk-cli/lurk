"""Parser for video editing apps."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class VideoEditorsParser(AppParser):
    name = "video_editors"
    app_names = ["Final Cut Pro", "DaVinci Resolve", "Adobe Premiere Pro"]
    bundle_ids = [
        "com.apple.FinalCut",
        "com.blackmagic-design.DaVinciResolve",
        "com.adobe.PremierePro",
    ]

    _suffixes = [
        " — Final Cut Pro",
        " - Final Cut Pro",
        " — DaVinci Resolve",
        " - DaVinci Resolve",
        " — Adobe Premiere Pro",
        " - Adobe Premiere Pro",
    ]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="video_editing",
        )

        if not title:
            return ctx

        cleaned = title
        for suffix in self._suffixes:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break

        ctx.document_name = cleaned

        return ctx
