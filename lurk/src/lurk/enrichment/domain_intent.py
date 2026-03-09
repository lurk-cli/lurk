"""Domain→intent mapping for enriching browsing activity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class DomainIntent:
    domain: str
    intent: str
    category: str
    confidence: float


# (intent, category) tuples keyed by domain pattern.
# Ordered so that more-specific paths come before their parent domains.
DOMAIN_INTENTS: dict[str, tuple[str, str]] = {
    # Social / Distribution
    "reddit.com/r/sideproject": ("launch/distribution", "social"),
    "reddit.com/r/startups": ("launch/distribution", "social"),
    "reddit.com/r/programming": ("community/distribution", "social"),
    "reddit.com/r/webdev": ("community/distribution", "social"),
    "reddit.com": ("community/browsing", "social"),
    "news.ycombinator.com": ("community/distribution", "social"),
    "producthunt.com": ("launch/distribution", "social"),
    "indiehackers.com": ("launch/distribution", "social"),
    "twitter.com": ("social/engagement", "social"),
    "x.com": ("social/engagement", "social"),
    "linkedin.com": ("professional/networking", "social"),
    # AI Tools
    "claude.ai": ("ai_tool/conversation", "ai_tool"),
    "chatgpt.com": ("ai_tool/conversation", "ai_tool"),
    "chat.openai.com": ("ai_tool/conversation", "ai_tool"),
    "gemini.google.com": ("ai_tool/conversation", "ai_tool"),
    "perplexity.ai": ("ai_tool/research", "ai_tool"),
    "copilot.microsoft.com": ("ai_tool/conversation", "ai_tool"),
    # Code / Reference
    "github.com": ("code/reference", "code"),
    "gitlab.com": ("code/reference", "code"),
    "bitbucket.org": ("code/reference", "code"),
    "stackoverflow.com": ("research/troubleshooting", "reference"),
    "docs.python.org": ("research/documentation", "reference"),
    "developer.mozilla.org": ("research/documentation", "reference"),
    "npmjs.com": ("research/packages", "reference"),
    "pypi.org": ("research/packages", "reference"),
    "crates.io": ("research/packages", "reference"),
    # Documentation
    "docs.anthropic.com": ("research/documentation", "reference"),
    "platform.openai.com": ("research/documentation", "reference"),
    "kubernetes.io": ("research/documentation", "reference"),
    "reactjs.org": ("research/documentation", "reference"),
    "nextjs.org": ("research/documentation", "reference"),
    # Design
    "figma.com": ("design/collaboration", "design"),
    "dribbble.com": ("design/inspiration", "design"),
    "behance.net": ("design/inspiration", "design"),
    # Project Management
    "linear.app": ("planning/tracking", "planning"),
    "notion.so": ("planning/documentation", "planning"),
    "asana.com": ("planning/tracking", "planning"),
    "trello.com": ("planning/tracking", "planning"),
    "jira.atlassian.com": ("planning/tracking", "planning"),
    # Google Workspace
    "docs.google.com": ("writing/documentation", "productivity"),
    "sheets.google.com": ("data/analysis", "productivity"),
    "slides.google.com": ("writing/presentation", "productivity"),
    "mail.google.com": ("communication/email", "communication"),
    "calendar.google.com": ("planning/scheduling", "planning"),
    # Communication
    "slack.com": ("communication/messaging", "communication"),
    "teams.microsoft.com": ("communication/messaging", "communication"),
    "discord.com": ("community/messaging", "communication"),
    # Analytics / Data
    "analytics.google.com": ("data/analytics", "data"),
    "mixpanel.com": ("data/analytics", "data"),
    "amplitude.com": ("data/analytics", "data"),
    # Shopping / Non-work
    "amazon.com": ("personal/shopping", "personal"),
    "youtube.com": ("media/entertainment", "personal"),
}

# Pre-sorted keys: longest (most specific) first so path-bearing entries
# match before their bare-domain parents.
_SORTED_KEYS = sorted(DOMAIN_INTENTS.keys(), key=len, reverse=True)

# Separators commonly used in browser tab titles.
_TITLE_SEP = re.compile(r"\s[-|—]\s")


def _extract_domain_and_path(url_or_domain: str) -> str:
    """Return ``host/path`` (no scheme, no query) for matching."""
    raw = url_or_domain.strip()
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").lower().lstrip("www.")
        path = parsed.path.rstrip("/")
        return f"{host}{path}" if path else host
    # Bare domain or domain/path — strip leading www.
    raw = raw.lower().lstrip("www.")
    return raw.split("?")[0].split("#")[0].rstrip("/")


def classify_domain_intent(url_or_domain: str) -> DomainIntent | None:
    """Classify a URL or bare domain into a *DomainIntent*.

    Checks exact matches first, then prefix/partial matches against
    ``DOMAIN_INTENTS``.  Returns ``None`` when no pattern matches.
    """
    normalised = _extract_domain_and_path(url_or_domain)
    if not normalised:
        return None

    # Exact match (fast path).
    hit = DOMAIN_INTENTS.get(normalised)
    if hit:
        return DomainIntent(
            domain=normalised, intent=hit[0], category=hit[1], confidence=1.0,
        )

    # Prefix / partial: walk from most-specific to least-specific.
    for pattern in _SORTED_KEYS:
        if normalised.startswith(pattern) or normalised.endswith(pattern):
            intent, category = DOMAIN_INTENTS[pattern]
            # Slightly lower confidence for partial matches.
            return DomainIntent(
                domain=pattern, intent=intent, category=category, confidence=0.8,
            )

    return None


def classify_title_intent(title: str, app: str) -> dict | None:
    """Extract semantic signal from a window title.

    Returns a dict with ``topic``, ``source``, and ``intent`` keys, or
    ``None`` when no useful signal can be extracted.
    """
    if not title:
        return None

    app_lower = app.lower() if app else ""

    # --- Browser tabs ---
    if any(
        kw in app_lower
        for kw in ("chrome", "firefox", "safari", "arc", "brave", "edge", "browser")
    ):
        parts = _TITLE_SEP.split(title)
        topic = parts[0].strip() if parts else title.strip()
        source = parts[-1].strip() if len(parts) > 1 else None
        if not topic:
            return None
        return {"topic": topic, "source": source or app, "intent": "browsing"}

    # --- AI chat apps ---
    if any(kw in app_lower for kw in ("claude", "chatgpt", "copilot", "gemini")):
        parts = _TITLE_SEP.split(title)
        topic = parts[0].strip() if parts else title.strip()
        return {"topic": topic, "source": app, "intent": "ai_conversation"}

    # --- Code editors ---
    if any(
        kw in app_lower
        for kw in ("code", "cursor", "xcode", "intellij", "pycharm", "sublime", "vim", "neovim")
    ):
        # Typical format: "filename — project" or "project - filename"
        parts = _TITLE_SEP.split(title)
        if len(parts) >= 2:
            return {
                "topic": parts[0].strip(),
                "source": parts[-1].strip(),
                "intent": "coding",
            }
        return {"topic": title.strip(), "source": app, "intent": "coding"}

    # --- Terminal ---
    if any(kw in app_lower for kw in ("terminal", "iterm", "warp", "alacritty", "kitty")):
        return {"topic": title.strip(), "source": app, "intent": "terminal"}

    return None


# ---- Session pattern detection ----

_CODING_KEYWORDS = {"coding", "code", "terminal", "editor", "vim", "cursor", "xcode"}
_BROWSING_KEYWORDS = {"browsing", "browser", "chrome", "safari", "firefox", "arc"}
_DOCS_KEYWORDS = {"docs", "documentation", "stackoverflow", "reference", "research"}
_COMMS_KEYWORDS = {"slack", "teams", "discord", "email", "mail", "messaging", "communication"}
_PLANNING_KEYWORDS = {"linear", "notion", "jira", "asana", "trello", "planning", "tracking"}
_DISTRIBUTION_KEYWORDS = {"reddit", "hackernews", "producthunt", "indiehackers", "launch", "distribution"}
_REVIEW_KEYWORDS = {"pull request", "pr review", "code review", "review", "diff"}


def _tag(breadcrumb: str) -> set[str]:
    """Return a set of category tags for a single breadcrumb string."""
    lower = breadcrumb.lower()
    tags: set[str] = set()
    if any(kw in lower for kw in _CODING_KEYWORDS):
        tags.add("code")
    if any(kw in lower for kw in _BROWSING_KEYWORDS):
        tags.add("browse")
    if any(kw in lower for kw in _DOCS_KEYWORDS):
        tags.add("docs")
    if any(kw in lower for kw in _COMMS_KEYWORDS):
        tags.add("comms")
    if any(kw in lower for kw in _PLANNING_KEYWORDS):
        tags.add("planning")
    if any(kw in lower for kw in _DISTRIBUTION_KEYWORDS):
        tags.add("distribution")
    if any(kw in lower for kw in _REVIEW_KEYWORDS):
        tags.add("review")
    return tags


def get_session_pattern(breadcrumbs: list[str]) -> str:
    """Detect a named sequence pattern from recent activity descriptions.

    Returns one of: ``"deep_work"``, ``"research_interrupt"``,
    ``"debugging_loop"``, ``"planning_mode"``, ``"launch_mode"``,
    ``"context_gathering"``, ``"review_mode"``, ``"exploring"``, or
    ``"mixed"``.
    """
    if not breadcrumbs:
        return "mixed"

    tagged = [_tag(b) for b in breadcrumbs]
    n = len(tagged)

    code_count = sum(1 for t in tagged if "code" in t)
    browse_count = sum(1 for t in tagged if "browse" in t)
    docs_count = sum(1 for t in tagged if "docs" in t)
    comms_count = sum(1 for t in tagged if "comms" in t)
    planning_count = sum(1 for t in tagged if "planning" in t)
    dist_count = sum(1 for t in tagged if "distribution" in t)
    review_count = sum(1 for t in tagged if "review" in t)

    # Count context switches (adjacent breadcrumbs with different tag sets).
    switches = sum(1 for i in range(1, n) if tagged[i] != tagged[i - 1])

    # deep_work — sustained single-app coding, few switches.
    if code_count > 10 and switches < n * 0.2:
        return "deep_work"

    # review_mode — PR / code review dominates.
    if review_count >= n * 0.3:
        return "review_mode"

    # launch_mode — distribution platforms mixed with coding.
    if dist_count >= 2 and code_count >= 2:
        return "launch_mode"

    # debugging_loop — code → terminal → docs/SO → code (repeated).
    if code_count >= 3 and docs_count >= 2 and switches >= n * 0.4:
        return "debugging_loop"

    # research_interrupt — coding with browsing/lookup interludes.
    if code_count >= 3 and browse_count >= 2 and switches >= n * 0.3:
        return "research_interrupt"

    # planning_mode — docs + comms heavy, little coding.
    if (planning_count + comms_count + docs_count) >= n * 0.5 and code_count < n * 0.2:
        return "planning_mode"

    # context_gathering — many different categories, lots of switching.
    distinct_categories = set()
    for t in tagged:
        distinct_categories.update(t)
    if len(distinct_categories) >= 4 and switches >= n * 0.5:
        return "context_gathering"

    # exploring — many quick switches, browsing-heavy.
    if browse_count >= n * 0.4 and switches >= n * 0.5:
        return "exploring"

    return "mixed"
