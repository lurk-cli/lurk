"""Parser for messaging apps (WeChat, WhatsApp, Telegram, LINE, Signal)."""

from __future__ import annotations

import re

from .base import AppParser, ParsedContext


class MessagingParser(AppParser):
    name = "messaging"
    app_names = ["WeChat", "微信", "WhatsApp", "Telegram", "Signal"]
    bundle_ids = [
        "com.tencent.xinWeChat",
        "net.whatsapp.WhatsApp",
        "ru.keepcoder.Telegram",
        "jp.naver.line.mac",
        "org.whispersystems.signal-desktop",
    ]

    # Title formats vary by app:
    # WeChat: "Contact Name" or "Contact Name - WeChat" or "Group Name (N)"
    # WhatsApp: "Contact Name" or "Group Name"
    # Telegram: "Contact Name" or "Group Name"
    # LINE: "Contact Name" or "Group Name"
    # Signal: "Contact Name" or "Group Name - Signal"

    _SUFFIX_RE = re.compile(
        r"\s*[-–—]\s*(?:WeChat|微信|WhatsApp|Telegram|LINE|Signal)\s*$",
        re.IGNORECASE,
    )
    _GROUP_COUNT_RE = re.compile(r"\s*\(\d+\)\s*$")

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="communicating",
            sub_activity="messaging",
        )

        if not title:
            return ctx

        # Strip app suffix
        cleaned = self._SUFFIX_RE.sub("", title).strip()

        # Strip group member count like "(5)"
        cleaned = self._GROUP_COUNT_RE.sub("", cleaned).strip()

        if cleaned:
            ctx.channel = cleaned
            ctx.topic = cleaned

        return ctx
