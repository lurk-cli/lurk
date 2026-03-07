"""Parser for Sketch."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class SketchParser(AppParser):
    name = "sketch"
    app_names = ["Sketch"]
    bundle_ids = ["com.bohemiancoding.sketch3"]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="designing",
        )

        if not title or title == "Sketch":
            return ctx

        # Sketch title: "Page — File.sketch"
        cleaned = title
        for suffix in [" — Sketch", " - Sketch"]:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break

        parts = [p.strip() for p in cleaned.split(" — ")]
        if len(parts) >= 2:
            ctx.document_name = parts[1]
            ctx.file = parts[0]
        elif parts:
            ctx.document_name = parts[0]

        return ctx
