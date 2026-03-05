"""Parser for VS Code, Cursor, and similar Electron-based editors."""

from __future__ import annotations

import re

from .base import AppParser, ParsedContext, detect_file_role, language_from_filename


class VSCodeParser(AppParser):
    name = "vscode"
    app_names = ["Visual Studio Code", "Code", "Cursor", "VSCodium", "Windsurf"]
    bundle_ids = [
        "com.microsoft.VSCode",
        "com.todesktop.230313mzl4w4u92",  # Cursor
        "com.vscodium",
    ]

    # Pattern: [●] filename [— folder] — AppName
    _ticket_re = re.compile(r"(?:^|\b)([A-Z]{2,10}-\d+)\b")
    _branch_re = re.compile(r"\(([^)]+)\)")

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(app=app, parser_name=self.name, activity="coding")

        if not title:
            return ctx

        # Split on em dash
        parts = [p.strip() for p in title.split(" — ")]

        if not parts:
            return ctx

        # First part is the filename (possibly with ● for unsaved)
        file_part = parts[0]
        if file_part.startswith("●"):
            ctx.unsaved = True
            file_part = file_part.lstrip("● ")

        # Could be "Welcome" tab or settings
        if file_part.lower() in ("welcome", "settings", "extensions", "get started"):
            ctx.sub_activity = "configuring"
            return ctx

        ctx.file = file_part

        # Detect language from extension
        ctx.language = language_from_filename(file_part)

        # Detect file role → sub_activity
        role = detect_file_role(file_part)
        if role == "testing":
            ctx.sub_activity = "testing"
        elif role == "documentation":
            ctx.sub_activity = "documentation"

        # Second part is typically the project/folder
        if len(parts) >= 3:
            ctx.project = parts[1]
        elif len(parts) == 2:
            # parts[1] is the app name — project might be embedded
            pass

        # Look for ticket references in the full title
        ticket_match = self._ticket_re.search(title)
        if ticket_match:
            ctx.ticket = ticket_match.group(1)

        # Look for branch in parentheses
        branch_match = self._branch_re.search(title)
        if branch_match:
            ctx.branch = branch_match.group(1)

        return ctx
