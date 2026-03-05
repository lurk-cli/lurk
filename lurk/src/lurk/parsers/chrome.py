"""Parser for Chrome, Brave, Edge, and Chromium-based browsers."""

from __future__ import annotations

import re

from .base import AppParser, ParsedContext


class ChromeParser(AppParser):
    name = "chrome"
    app_names = ["Google Chrome", "Brave Browser", "Microsoft Edge", "Chromium"]
    bundle_ids = [
        "com.google.Chrome",
        "com.brave.Browser",
        "com.microsoft.edgemac",
    ]

    _ticket_re = re.compile(r"(?:^|\b)([A-Z]{2,10}-\d+)\b")
    _pr_re = re.compile(r"#(\d+)")

    # Domain → activity classification
    _domain_activity: dict[str, tuple[str, str | None]] = {
        "stackoverflow.com": ("researching", "stack_overflow"),
        "github.com": ("coding", None),
        "gitlab.com": ("coding", None),
        "docs.google.com": ("writing", "document"),
        "mail.google.com": ("communicating", "email"),
        "outlook.live.com": ("communicating", "email"),
        "outlook.office.com": ("communicating", "email"),
        "figma.com": ("designing", None),
        "notion.so": ("writing", "notes"),
        "linear.app": ("coding", "project_management"),
        "jira.atlassian.com": ("coding", "project_management"),
        "confluence.atlassian.com": ("researching", "documentation"),
        "twitter.com": ("browsing", "social"),
        "x.com": ("browsing", "social"),
        "reddit.com": ("browsing", "social"),
        "youtube.com": ("browsing", "video"),
        "news.ycombinator.com": ("browsing", "social"),
    }

    # Title patterns for activity detection
    _research_patterns = [
        (re.compile(r"Stack Overflow", re.I), "researching", "stack_overflow"),
        (re.compile(r"MDN Web Docs", re.I), "researching", "documentation"),
        (re.compile(r"documentation|docs|api reference", re.I), "researching", "documentation"),
        (re.compile(r"tutorial|guide|how to", re.I), "researching", "learning"),
        (re.compile(r"Pull Request|Merge Request", re.I), "coding", "code_review"),
        (re.compile(r"Issues? ·|Bug Report", re.I), "coding", "issue_tracking"),
    ]

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(app=app, parser_name=self.name, activity="browsing")

        if not title:
            return ctx

        # Strip browser name suffix
        for suffix in [" — Google Chrome", " — Brave", " — Microsoft Edge",
                       " - Google Chrome", " - Brave", " - Microsoft Edge"]:
            if title.endswith(suffix):
                title = title[: -len(suffix)]
                break

        ctx.topic = title

        # Try to detect domain from title patterns
        for domain, (activity, sub) in self._domain_activity.items():
            domain_short = domain.split(".")[0]
            if domain_short.lower() in title.lower():
                ctx.activity = activity
                ctx.sub_activity = sub
                ctx.url_domain = domain
                break

        # Try research patterns
        for pattern, activity, sub in self._research_patterns:
            if pattern.search(title):
                ctx.activity = activity
                ctx.sub_activity = sub
                break

        # GitHub-specific parsing
        if "github.com" in (ctx.url_domain or "") or "GitHub" in title:
            ctx.url_domain = "github.com"
            if "Pull Request" in title or "PR" in title:
                ctx.activity = "coding"
                ctx.sub_activity = "code_review"
                pr_match = self._pr_re.search(title)
                if pr_match:
                    ctx.ticket = f"#{pr_match.group(1)}"
            elif "Issues" in title:
                ctx.activity = "coding"
                ctx.sub_activity = "issue_tracking"

        # Look for ticket patterns
        ticket_match = self._ticket_re.search(title)
        if ticket_match:
            ctx.ticket = ticket_match.group(1)

        return ctx
