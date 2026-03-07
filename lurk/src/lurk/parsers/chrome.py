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
        # Google Workspace
        "docs.google.com": ("writing", "document"),
        "sheets.google.com": ("spreadsheet_work", "spreadsheet"),
        "slides.google.com": ("writing", "presentation"),
        "drive.google.com": ("browsing", "file_management"),
        "mail.google.com": ("communicating", "email"),
        "calendar.google.com": ("planning", "calendar"),
        "meet.google.com": ("meeting", "video_call"),
        "forms.google.com": ("writing", "form"),
        # Other email/productivity
        "outlook.live.com": ("communicating", "email"),
        "outlook.office.com": ("communicating", "email"),
        "figma.com": ("designing", None),
        "notion.so": ("writing", "notes"),
        "linear.app": ("planning", "project_management"),
        "jira.atlassian.com": ("planning", "project_management"),
        "confluence.atlassian.com": ("researching", "documentation"),
        "twitter.com": ("browsing", "social"),
        "x.com": ("browsing", "social"),
        "reddit.com": ("browsing", "social"),
        "youtube.com": ("browsing", "video"),
        "news.ycombinator.com": ("browsing", "social"),
        # Project Management
        "asana.com": ("planning", "project_management"),
        "monday.com": ("planning", "project_management"),
        "trello.com": ("planning", "project_management"),
        "productboard.com": ("planning", "product_strategy"),
        "shortcut.com": ("planning", "project_management"),
        "clickup.com": ("planning", "project_management"),
        # Design (web)
        "canva.com": ("designing", "graphic_design"),
        "miro.com": ("designing", "whiteboarding"),
        "whimsical.com": ("designing", "whiteboarding"),
        # Data / Analytics
        "looker.com": ("data_analysis", "dashboard"),
        "mixpanel.com": ("data_analysis", "analytics"),
        "amplitude.com": ("data_analysis", "analytics"),
        "analytics.google.com": ("data_analysis", "analytics"),
        # Marketing
        "hubspot.com": ("marketing", "crm"),
        "mailchimp.com": ("marketing", "email_campaign"),
        "hootsuite.com": ("marketing", "social_media"),
        "buffer.com": ("marketing", "social_media"),
        "semrush.com": ("marketing", "seo"),
        "ahrefs.com": ("marketing", "seo"),
        # Sales
        "salesforce.com": ("sales", "crm"),
        "gong.io": ("sales", "call_review"),
        "outreach.io": ("sales", "outreach"),
        # Support
        "zendesk.com": ("support", "ticket"),
        "intercom.io": ("support", "chat"),
        "freshdesk.com": ("support", "ticket"),
    }

    # Google Workspace title patterns for extracting document names
    _google_doc_re = re.compile(r"^(.+?)\s*[-–—]\s*Google (?:Docs|Sheets|Slides|Forms)")
    _gmail_re = re.compile(r"^(.+?)\s*[-–—]\s*.*mail\.google\.com|^Gmail\b|^Inbox\b")
    _gcal_re = re.compile(r"^Google Calendar\b|^(.+?)\s*[-–—]\s*Google Calendar")

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

        # Google Workspace — extract document/sheet/slide names from titles
        google_doc_match = self._google_doc_re.match(title)
        if google_doc_match:
            ctx.document_name = google_doc_match.group(1).strip()

        # Gmail — detect compose vs triage
        if ctx.url_domain == "mail.google.com":
            lower = title.lower()
            if any(w in lower for w in ["inbox", "sent", "drafts", "starred", "all mail"]):
                ctx.sub_activity = "email_triage"
            elif "compose" in lower or "new message" in lower:
                ctx.sub_activity = "email_composing"
            else:
                # Likely reading a specific email — title is subject line
                ctx.sub_activity = "email_reading"
                ctx.topic = title.split(" - ")[0].strip() if " - " in title else title

        # Google Calendar — detect event vs browsing
        if ctx.url_domain == "calendar.google.com":
            gcal_match = self._gcal_re.match(title)
            if gcal_match and gcal_match.group(1):
                ctx.topic = gcal_match.group(1).strip()

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
