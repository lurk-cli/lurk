"""Parser for Figma."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class FigmaParser(AppParser):
    name = "figma"
    app_names = ["Figma"]
    bundle_ids = ["com.figma.Desktop"]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="designing",
        )

        if not title or title == "Figma":
            return ctx

        # Figma title: "Page Name – File Name — Figma"
        cleaned = title
        for suffix in [" — Figma", " - Figma"]:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break

        parts = [p.strip() for p in cleaned.split(" – ")]
        if len(parts) >= 2:
            ctx.document_name = parts[1]  # File name
            ctx.file = parts[0]  # Page name
        elif parts:
            ctx.document_name = parts[0]

        return ctx
