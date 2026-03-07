"""Parser for Adobe Creative Suite apps."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class AdobeCreativeParser(AppParser):
    name = "adobe_creative"
    app_names = [
        "Adobe Photoshop", "Adobe Illustrator",
        "Adobe InDesign", "Adobe After Effects",
    ]
    bundle_ids = [
        "com.adobe.Photoshop",
        "com.adobe.Illustrator",
        "com.adobe.InDesign",
        "com.adobe.AfterEffects",
    ]

    _video_apps = {"after effects", "aftereffects"}

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        # After Effects → video_editing, rest → designing
        app_lower = app.lower()
        activity = "video_editing" if any(v in app_lower for v in self._video_apps) else "designing"

        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity=activity,
        )

        if not title:
            return ctx

        # Adobe titles: "file.psd @ 100% (Layer, RGB/8)" → strip after @
        cleaned = title
        at_idx = cleaned.find(" @ ")
        if at_idx > 0:
            cleaned = cleaned[:at_idx]

        # Strip app suffix
        for suffix in [" — Adobe Photoshop", " - Adobe Photoshop",
                       " — Adobe Illustrator", " - Adobe Illustrator",
                       " — Adobe InDesign", " - Adobe InDesign",
                       " — Adobe After Effects", " - Adobe After Effects"]:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break

        ctx.document_name = cleaned.strip()

        return ctx
