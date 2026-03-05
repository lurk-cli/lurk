"""Parser for Linear project management."""

from __future__ import annotations

import re

from .base import AppParser, ParsedContext


class LinearParser(AppParser):
    name = "linear"
    app_names = ["Linear"]
    bundle_ids = ["com.linear"]

    _ticket_re = re.compile(r"^(?P<ticket>[A-Z]+-\d+)\s+(?P<title>.+?)(?:\s*[·—]\s*Linear)?$")

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="coding",
            sub_activity="project_management",
        )

        if not title or title == "Linear":
            return ctx

        match = self._ticket_re.match(title)
        if match:
            ctx.ticket = match.group("ticket")
            ctx.document_name = match.group("title")
        else:
            # Fallback: clean suffix
            cleaned = title
            for suffix in [" — Linear", " - Linear"]:
                if cleaned.endswith(suffix):
                    cleaned = cleaned[: -len(suffix)]
                    break
            ctx.document_name = cleaned

        return ctx
