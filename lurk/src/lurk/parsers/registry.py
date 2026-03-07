"""Parser registry — tries parsers in order until one matches."""

from __future__ import annotations

import logging

from .base import AppParser, ParsedContext
from .metrics import ParserMetrics
from .vscode import VSCodeParser
from .chrome import ChromeParser
from .safari import SafariParser
from .arc import ArcParser
from .firefox import FirefoxParser
from .slack import SlackParser
from .teams import TeamsParser
from .discord import DiscordParser
from .terminal import TerminalParser
from .figma import FigmaParser
from .notion import NotionParser
from .linear import LinearParser
from .xcode import XcodeParser
from .jetbrains import JetBrainsParser
from .mail import MailParser
from .zoom import ZoomParser
from .documents import DocumentsParser
from .sketch import SketchParser
from .video_editors import VideoEditorsParser
from .adobe_creative import AdobeCreativeParser
from .tableau import TableauParser
from .fallback import FallbackParser

logger = logging.getLogger("lurk.parsers")


class ParserRegistry:
    """Registry of app-specific title parsers."""

    def __init__(self) -> None:
        self.parsers: list[AppParser] = [
            VSCodeParser(),
            ChromeParser(),
            SafariParser(),
            ArcParser(),
            FirefoxParser(),
            SlackParser(),
            TeamsParser(),
            DiscordParser(),
            TerminalParser(),
            FigmaParser(),
            NotionParser(),
            LinearParser(),
            XcodeParser(),
            JetBrainsParser(),
            MailParser(),
            ZoomParser(),
            DocumentsParser(),
            SketchParser(),
            VideoEditorsParser(),
            AdobeCreativeParser(),
            TableauParser(),
            FallbackParser(),  # Always last — matches everything
        ]
        self.metrics = ParserMetrics()

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        """Parse a window title using the first matching parser."""
        for parser in self.parsers:
            if parser.can_parse(app, bundle_id):
                self.metrics.record_call(parser.name)
                try:
                    ctx = parser.parse(title, app, bundle_id)
                except Exception:
                    logger.exception("Parser '%s' raised on title %r", parser.name, title)
                    self.metrics.record_error(parser.name)
                    continue  # fall through to next parser

                if ctx.validate():
                    self.metrics.record_success(parser.name)
                else:
                    self.metrics.record_empty(parser.name)
                return ctx

        # FallbackParser always matches, so we should never get here
        return ParsedContext(app=app)
