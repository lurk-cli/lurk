"""Document screen OCR analyzer — extracts structured content from document app screenshots.

Uses geometric heuristics on OCR text blocks (with bounding boxes from Vision)
to identify headings, key content, lists, and tables in document editing apps
like Google Docs, Notion, Word, etc.
No ML required — just spatial reasoning about document UI layouts.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field

from .spatial import TextBlock


@dataclass
class DocumentExtract:
    """Structured context extracted from a document app screen."""
    app: str                         # Google Docs, Notion, Word, etc.
    document_name: str               # extracted from title bar
    doc_type: str                    # spreadsheet | presentation | doc | wiki | notes
    headings: list[dict] = field(default_factory=list)       # [{"level": 1, "text": "..."}, ...]
    key_content: list[str] = field(default_factory=list)     # high-signal text fragments
    editing_section: str | None = None   # which section appears active/focused
    lists_and_bullets: list[str] = field(default_factory=list)  # bullet points, numbered lists
    tables_detected: bool = False        # whether tabular data was seen
    dedupe_hash: str = ""                # for cross-capture deduplication


# ---------------------------------------------------------------------------
# App detection
# ---------------------------------------------------------------------------

DOCUMENT_APPS = {
    "Google Docs", "Google Sheets", "Google Slides",
    "Notion", "Obsidian", "Bear", "Apple Notes", "Notes",
    "Microsoft Word", "Word", "Microsoft Excel", "Excel",
    "Microsoft PowerPoint", "PowerPoint", "Keynote", "Pages", "Numbers",
    "Confluence", "Coda", "Quip", "Dropbox Paper",
}

# App name → doc type mapping
_DOC_TYPE_MAP: dict[str, str] = {
    "sheets": "spreadsheet",
    "excel": "spreadsheet",
    "numbers": "spreadsheet",
    "slides": "presentation",
    "powerpoint": "presentation",
    "keynote": "presentation",
    "notion": "wiki",
    "confluence": "wiki",
    "coda": "wiki",
    "notes": "notes",
    "apple notes": "notes",
    "bear": "notes",
    "obsidian": "notes",
}

# Bullet/list prefixes
_BULLET_RE = re.compile(
    r"^(?:[\u2022\u2023\u25E6\u25AA\u25AB\u2043\u2219•·◦▪▫‣]\s*"  # bullet chars
    r"|\d{1,3}[.)]\s+"       # numbered: 1. or 1)
    r"|[a-zA-Z][.)]\s+"      # lettered: a. or a)
    r"|[-–—]\s+)"            # dashes
)

# Action-item / high-signal patterns
_HIGH_SIGNAL_RE = re.compile(
    r"(?:TODO|FIXME|NOTE|Action|DECISION|BLOCKED|DONE|WIP|Q\d|FY\d)"
    r"|(?:\[\s*[xX ]?\s*\])"    # checkbox: [ ] or [x]
    r"|(?:\$\s*[\d,.]+[MKBmkb]?)"  # money amounts
    r"|(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4})"  # dates
    r"|(?:@\w+)",              # mentions
    re.IGNORECASE,
)

# App name suffixes to strip from window title
_TITLE_SUFFIXES = re.compile(
    r"\s*[-–—]\s*(?:Google Docs|Google Sheets|Google Slides|Notion|Obsidian|Bear"
    r"|Microsoft Word|Microsoft Excel|Microsoft PowerPoint|Word|Excel|PowerPoint"
    r"|Keynote|Pages|Numbers|Confluence|Coda|Quip|Dropbox Paper|Apple Notes|Notes)\s*$",
    re.IGNORECASE,
)

# File extension pattern
_FILE_EXT_RE = re.compile(r"\.\w{2,5}$")

# Module-level dedup cache: hash → timestamp
_seen_hashes: dict[str, float] = {}
_MAX_SEEN = 200
_DEDUP_WINDOW = 300.0  # 5 minutes


def is_document_app(app_name: str) -> bool:
    """Check if the app is a document editing application."""
    if not app_name:
        return False
    app_lower = app_name.lower()
    for doc_app in DOCUMENT_APPS:
        if doc_app.lower() in app_lower:
            return True
    return False


def _infer_doc_type(app: str) -> str:
    """Infer document type from app name."""
    app_lower = app.lower()
    for key, dtype in _DOC_TYPE_MAP.items():
        if key in app_lower:
            return dtype
    return "doc"


def _extract_document_name(title: str, app: str) -> str:
    """Extract the document name from a window title.

    Strips app name suffixes, file extensions, and common decorators.
    """
    if not title:
        return "Untitled"

    name = _TITLE_SUFFIXES.sub("", title).strip()
    # Strip file extensions like .docx, .xlsx, .pptx
    name = _FILE_EXT_RE.sub("", name).strip()
    # Strip leading/trailing whitespace and decorators
    name = name.strip(" -–—·")
    return name if name else "Untitled"


def _is_ui_chrome(block: TextBlock) -> bool:
    """Filter out UI chrome: menu bar, sidebar, status bar.

    Vision uses bottom-left origin:
    - y > 0.95 = top of screen (menu bar area)
    - y < 0.03 = bottom of screen (status bar)
    - x < 0.15 with narrow width = sidebar
    """
    # Top ~5% = menu/toolbar area (y + h > 0.95 in bottom-left origin)
    if block.y + block.h > 0.95:
        return True
    # Bottom ~3% = status bar
    if block.y < 0.03:
        return True
    # Left sidebar: narrow blocks in left 15% of screen
    if block.x < 0.15 and block.w < 0.15:
        return True
    return False


def _detect_headings(content_blocks: list[TextBlock]) -> list[dict]:
    """Detect headings by relative text block height (font size proxy).

    Larger h values suggest larger font → heading. We use relative sizing
    to classify into heading levels.
    """
    if not content_blocks:
        return []

    # Compute median block height for baseline
    heights = sorted(b.h for b in content_blocks)
    median_h = heights[len(heights) // 2] if heights else 0
    if median_h <= 0:
        return []

    headings: list[dict] = []

    for block in content_blocks:
        text = block.text.strip()
        if not text or len(text) > 120:
            # Headings are generally short
            continue

        ratio = block.h / median_h
        if ratio < 1.3:
            # Not notably larger than body text
            continue

        # Determine heading level by size ratio
        if ratio >= 2.0:
            level = 1
        elif ratio >= 1.6:
            level = 2
        else:
            level = 3

        # Additional heuristic: headings tend to be short relative to width
        # (few words spanning the available space)
        char_density = len(text) / max(block.w, 0.01)
        if char_density > 300:
            # Very dense text — probably body, not heading
            continue

        headings.append({"level": level, "text": text})

    # Sort by vertical position (top to bottom in bottom-left origin = descending y)
    # We want top-of-document first
    headings_with_y = []
    for block in content_blocks:
        for h in headings:
            if block.text.strip() == h["text"]:
                headings_with_y.append((block.y, h))
                break

    headings_with_y.sort(key=lambda x: -x[0])  # descending y = top first
    return [h for _, h in headings_with_y]


def _detect_lists(content_blocks: list[TextBlock]) -> list[str]:
    """Detect bullet points and numbered lists from text blocks."""
    items: list[str] = []
    for block in content_blocks:
        text = block.text.strip()
        if _BULLET_RE.match(text):
            items.append(text)
    return items


def _detect_tables(content_blocks: list[TextBlock]) -> bool:
    """Detect table-like grid arrangements.

    Look for multiple blocks at approximately the same y-coordinate
    with regular x spacing — suggests tabular layout.
    """
    if len(content_blocks) < 6:
        return False

    # Group blocks by approximate y position (within tolerance)
    y_tolerance = 0.015
    rows: dict[float, list[TextBlock]] = {}
    for block in content_blocks:
        placed = False
        for row_y in rows:
            if abs(block.y - row_y) < y_tolerance:
                rows[row_y].append(block)
                placed = True
                break
        if not placed:
            rows[block.y] = [block]

    # Count rows with 3+ blocks at different x positions — table indicator
    table_rows = 0
    for row_blocks in rows.values():
        if len(row_blocks) >= 3:
            # Check that x positions are spread out (not all stacked)
            x_positions = sorted(b.x for b in row_blocks)
            spread = x_positions[-1] - x_positions[0]
            if spread > 0.3:
                table_rows += 1

    return table_rows >= 2


def _extract_key_content(content_blocks: list[TextBlock]) -> list[str]:
    """Extract high-signal text fragments: action items, numbers, dates, mentions."""
    key: list[str] = []
    for block in content_blocks:
        text = block.text.strip()
        if not text or len(text) < 4:
            continue
        if _HIGH_SIGNAL_RE.search(text):
            key.append(text[:200])
            if len(key) >= 15:
                break
    return key


def _compute_dedupe_hash(content_blocks: list[TextBlock]) -> str:
    """Compute a dedup hash from the first 100 chars of each content block, sorted."""
    snippets = sorted(b.text.strip()[:100] for b in content_blocks if b.text.strip())
    raw = "|".join(snippets)
    return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_duplicate(h: str) -> bool:
    """Check if this hash was seen recently. Maintains a bounded cache."""
    global _seen_hashes
    now = time.time()

    # Evict old entries
    if len(_seen_hashes) > _MAX_SEEN:
        cutoff = now - _DEDUP_WINDOW
        _seen_hashes = {k: v for k, v in _seen_hashes.items() if v > cutoff}

    if h in _seen_hashes and (now - _seen_hashes[h]) < _DEDUP_WINDOW:
        return True

    _seen_hashes[h] = now
    return False


def analyze_document_screen(
    blocks: list[TextBlock], app: str, title: str,
) -> DocumentExtract | None:
    """Analyze OCR text blocks from a document app screenshot.

    Returns None if the screen doesn't contain meaningful document content
    or if it's a duplicate of a recent capture.
    """
    if not blocks or len(blocks) < 3:
        return None

    # Filter out UI chrome
    content_blocks = [b for b in blocks if not _is_ui_chrome(b)]
    if len(content_blocks) < 2:
        return None

    # Deduplication
    dedupe_hash = _compute_dedupe_hash(content_blocks)
    if _is_duplicate(dedupe_hash):
        return None

    doc_name = _extract_document_name(title, app)
    doc_type = _infer_doc_type(app)

    headings = _detect_headings(content_blocks)
    lists_and_bullets = _detect_lists(content_blocks)
    tables_detected = _detect_tables(content_blocks)
    key_content = _extract_key_content(content_blocks)

    # Editing section: approximate by finding the heading closest to
    # the vertical center of the screen (where the cursor likely is).
    # In bottom-left origin, center ~ y=0.5.
    editing_section = None
    if headings:
        # Find the heading block closest to vertical center
        center_y = 0.5
        best_heading = None
        best_dist = float("inf")
        for block in content_blocks:
            text = block.text.strip()
            for h in headings:
                if h["text"] == text:
                    dist = abs(block.y - center_y)
                    if dist < best_dist:
                        best_dist = dist
                        best_heading = h
                    break
        if best_heading:
            editing_section = best_heading["text"]

    return DocumentExtract(
        app=app,
        document_name=doc_name,
        doc_type=doc_type,
        headings=headings,
        key_content=key_content,
        editing_section=editing_section,
        lists_and_bullets=lists_and_bullets,
        tables_detected=tables_detected,
        dedupe_hash=dedupe_hash,
    )
