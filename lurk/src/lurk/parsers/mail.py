"""Parser for email clients."""

from __future__ import annotations

from .base import AppParser, ParsedContext


class MailParser(AppParser):
    name = "mail"
    app_names = ["Mail", "Outlook", "Spark", "Airmail"]
    bundle_ids = [
        "com.apple.mail",
        "com.microsoft.Outlook",
        "com.readdle.smartemail-macos",
        "it.bloop.airmail2",
    ]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="communicating",
            sub_activity="email",
        )

        if not title:
            return ctx

        lower = title.lower()
        if any(w in lower for w in ["inbox", "sent", "draft", "archive", "trash"]):
            ctx.sub_activity = "email_triage"
        elif "new message" in lower or "compose" in lower:
            ctx.sub_activity = "email_composing"
        else:
            # Title is likely the email subject — capture it as topic for context
            ctx.sub_activity = "email_reading"
            ctx.topic = title

        return ctx
