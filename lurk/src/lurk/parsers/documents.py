"""Parser for document and spreadsheet apps."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class DocumentsParser(AppParser):
    name = "documents"
    app_names = [
        "Microsoft Word", "Microsoft Excel", "Microsoft PowerPoint",
        "Pages", "Numbers", "Keynote",
        "Google Docs", "Google Sheets", "Google Slides",
        "Preview", "TextEdit",
    ]
    bundle_ids = [
        "com.microsoft.Word",
        "com.microsoft.Excel",
        "com.microsoft.Powerpoint",
        "com.apple.iWork.Pages",
        "com.apple.iWork.Numbers",
        "com.apple.iWork.Keynote",
        "com.apple.Preview",
        "com.apple.TextEdit",
    ]

    _app_activity: dict[str, tuple[str, str | None]] = {
        "word": ("writing", "document"),
        "pages": ("writing", "document"),
        "textedit": ("writing", "document"),
        "excel": ("spreadsheet_work", "spreadsheet"),
        "numbers": ("spreadsheet_work", "spreadsheet"),
        "powerpoint": ("writing", "presentation"),
        "keynote": ("writing", "presentation"),
        "preview": ("reading", None),
    }

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        # Determine activity from app
        activity = "writing"
        sub = None
        for key, (act, sub_act) in self._app_activity.items():
            if key in app.lower():
                activity = act
                sub = sub_act
                break

        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity=activity,
            sub_activity=sub,
        )

        if title:
            ctx.document_name = title

        return ctx
