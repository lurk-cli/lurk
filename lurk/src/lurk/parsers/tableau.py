"""Parser for Tableau Desktop."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class TableauParser(AppParser):
    name = "tableau"
    app_names = ["Tableau Desktop", "Tableau"]
    bundle_ids = ["com.tableau.Tableau"]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="data_analysis",
        )

        if not title or title in ("Tableau Desktop", "Tableau"):
            return ctx

        # Tableau title: "Workbook - Tableau Desktop"
        cleaned = title
        for suffix in [" — Tableau Desktop", " - Tableau Desktop",
                       " — Tableau", " - Tableau"]:
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
                break

        ctx.document_name = cleaned

        return ctx
