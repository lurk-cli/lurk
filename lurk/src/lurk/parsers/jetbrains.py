"""Parser for JetBrains IDEs (IntelliJ, WebStorm, PyCharm, GoLand, etc.)."""

from __future__ import annotations

from .base import AppParser, ParsedContext, language_from_filename, detect_file_role


class JetBrainsParser(AppParser):
    name = "jetbrains"
    app_names = [
        "IntelliJ IDEA", "WebStorm", "PyCharm", "GoLand",
        "RubyMine", "PhpStorm", "CLion", "DataGrip",
        "Rider", "Android Studio",
    ]
    bundle_ids = [
        "com.jetbrains.intellij",
        "com.jetbrains.WebStorm",
        "com.jetbrains.pycharm",
        "com.jetbrains.goland",
        "com.jetbrains.rubymine",
        "com.jetbrains.PhpStorm",
        "com.jetbrains.CLion",
        "com.jetbrains.DataGrip",
        "com.jetbrains.rider",
        "com.google.android.studio",
    ]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="coding",
        )

        if not title:
            return ctx

        # JetBrains title: "project — filename — IDE Name"
        # or: "project — [filepath] — IDE Name"
        parts = [p.strip() for p in title.split(" — ")]

        if len(parts) >= 3:
            ctx.project = parts[0]
            file_part = parts[1]
            # Strip directory path brackets [...]
            if file_part.startswith("[") and "]" in file_part:
                file_part = file_part.split("]")[-1].strip()
            ctx.file = file_part
            ctx.language = language_from_filename(file_part)
            role = detect_file_role(file_part)
            if role == "testing":
                ctx.sub_activity = "testing"
        elif len(parts) == 2:
            ctx.project = parts[0]

        return ctx
