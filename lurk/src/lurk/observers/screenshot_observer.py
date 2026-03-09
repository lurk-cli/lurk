"""Screenshot observer — reads screen captures and stores raw OCR text.

The daemon captures screenshots of the active window every ~10 seconds.
This observer reads those screenshots, runs OCR, and stores the full text
in a rolling buffer. The raw screen content is the highest-signal input
for prompt generation — it tells you exactly what the user is looking at.

When an LLM is available, the raw OCR text goes directly to prompt synthesis.
No regex extraction, no categorical labels, no intermediate destruction.
The LLM reads what's on screen and infers goal, state, and context in one pass.

The regex-based extraction (_extract_context_from_text) is kept only as a
fallback for rules-based prompt generation when no LLM is configured.

Uses macOS Vision framework (VNRecognizeTextRequest) for fast, local OCR.
No external dependencies, no cloud APIs, no data leaving the machine.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("lurk.observers.screenshot")

# ---------------------------------------------------------------------------
# Relevance classification — filter leisure content from work context
# ---------------------------------------------------------------------------

# Apps that are almost never work-relevant
_LEISURE_APPS: frozenset[str] = frozenset({
    # Video/streaming
    "youtube", "netflix", "hulu", "disney+", "prime video", "twitch",
    "vlc", "iina", "plex", "hbo max", "apple tv",
    # Gaming
    "steam", "epic games", "battle.net", "origin", "gog galaxy",
    "minecraft", "league of legends", "valorant", "fortnite",
    # Social media (non-work)
    "tiktok", "instagram", "snapchat", "reddit",
    # Shopping
    "amazon shopping",
    # Music (background, not workflow-relevant)
    "spotify", "apple music", "music",
})

# Apps that are always work-relevant (skip further classification)
_WORK_APPS: frozenset[str] = frozenset({
    "terminal", "iterm", "iterm2", "warp", "alacritty", "kitty",
    "visual studio code", "code", "cursor", "xcode", "intellij",
    "pycharm", "webstorm", "sublime text", "vim", "neovim", "emacs",
    "github desktop", "tower", "sourcetree", "fork",
    "slack", "microsoft teams", "zoom", "linear", "jira",
    "notion", "obsidian", "bear", "things", "todoist",
    "figma", "sketch", "postman", "insomnia", "tableplus", "datagrip",
})

# Bundle ID prefixes that indicate leisure
_LEISURE_BUNDLE_PREFIXES: list[str] = [
    "com.google.ios.youtube", "com.netflix", "com.valve.steam",
    "tv.twitch", "com.epicgames",
]

_BROWSER_APPS = frozenset({"google chrome", "safari", "firefox", "arc", "brave", "microsoft edge", "chromium", "opera", "vivaldi"})

# OCR patterns indicating leisure content in browsers
_LEISURE_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)(?:subscribe|subscribed)\s*\d*[KkMm]?"),  # YouTube subscribe buttons
    re.compile(r"(?i)(?:add to cart|buy now|checkout|shopping cart|your order)"),  # Shopping
    re.compile(r"(?i)(?:watch later|up next|recommended for you|trending now)"),  # Video streaming
    re.compile(r"(?i)(?:\d+:\d+\s*/\s*\d+:\d+)"),  # Video player timestamps
    re.compile(r"(?i)(?:game over|play again|high score|leaderboard|achievements)"),  # Gaming
    re.compile(r"(?i)(?:followers|following|retweet|repost|liked by)"),  # Social media
    re.compile(r"(?i)(?:free shipping|price drop|deal of the day|save \d+%)"),  # Shopping deals
]

# OCR patterns indicating work content in browsers
_WORK_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)(?:pull request|merge|commit|branch|repository|issue #\d+)"),  # Git/GitHub
    re.compile(r"(?i)(?:stack overflow|stackoverflow)"),
    re.compile(r"(?i)(?:documentation|api reference|getting started|changelog)"),
    re.compile(r"(?i)(?:def |class |function |import |const |let |var )"),  # Code
    re.compile(r"(?i)(?:jira|linear|asana|trello|confluence|notion)"),  # PM tools
    re.compile(r"(?i)(?:error|exception|traceback|debug|TypeError|ValueError)"),  # Debugging
    re.compile(r"(?i)(?:chatgpt|claude|copilot|cursor|openai|anthropic)"),  # AI tools (work)
    re.compile(r"(?i)(?:google docs|spreadsheet|slides|presentation)"),  # Productivity
]


def _is_browser(app_lower: str) -> bool:
    return app_lower in _BROWSER_APPS


def _classify_browser_content(title: str, text: str) -> str:
    """Classify browser content based on title and OCR text."""
    combined = f"{title}\n{text[:1000]}"  # first 1000 chars is enough

    work_hits = sum(1 for p in _WORK_PATTERNS if p.search(combined))
    leisure_hits = sum(1 for p in _LEISURE_PATTERNS if p.search(combined))

    # Also check title for common leisure domains
    title_lower = title.lower()
    leisure_title_keywords = ["youtube", "netflix", "twitch", "reddit", "tiktok",
                               "instagram", "amazon.com", "ebay", "etsy", "shopping"]
    for kw in leisure_title_keywords:
        if kw in title_lower:
            leisure_hits += 2  # title is a strong signal

    work_title_keywords = ["github", "gitlab", "stackoverflow", "docs", "api",
                            "jira", "linear", "slack", "notion", "figma", "chatgpt", "claude"]
    for kw in work_title_keywords:
        if kw in title_lower:
            work_hits += 2

    if work_hits > leisure_hits:
        return "work"
    if leisure_hits > work_hits:
        return "leisure"
    return "ambiguous"


def classify_frame_relevance(app: str, title: str, text: str, bundle_id: str = "") -> str:
    """Classify a screen frame's work relevance.

    Returns: "work", "leisure", or "ambiguous"

    - "work": definitely work-related, include in context
    - "leisure": definitely not work-related, exclude from context/workflows
    - "ambiguous": unclear, include but with lower weight
    """
    app_lower = app.lower().strip()

    # Fast path: known work apps
    if app_lower in _WORK_APPS:
        return "work"

    # Fast path: known leisure apps
    if app_lower in _LEISURE_APPS:
        return "leisure"

    # Bundle ID check
    bundle_lower = bundle_id.lower()
    for prefix in _LEISURE_BUNDLE_PREFIXES:
        if bundle_lower.startswith(prefix):
            return "leisure"

    # For browsers, check title and OCR text
    if _is_browser(app_lower):
        return _classify_browser_content(title, text)

    # Default: ambiguous (include with normal weight)
    return "ambiguous"


SNAPSHOT_DIR = Path.home() / ".lurk" / "snapshots"
SNAPSHOT_IMAGE = SNAPSHOT_DIR / "latest.jpg"  # backward compat single-display
SNAPSHOT_META = SNAPSHOT_DIR / "latest.json"

MIN_PROCESS_INTERVAL = 8.0  # seconds


def _find_display_snapshots() -> list[tuple[int, Path]]:
    """Find all display snapshot files (latest_0.jpg, latest_1.jpg, ...).

    Returns list of (display_id, path) sorted by display_id.
    Falls back to latest.jpg if no numbered snapshots exist.
    """
    numbered: list[tuple[int, Path]] = []
    for p in SNAPSHOT_DIR.glob("latest_*.jpg"):
        # Extract display id from latest_0.jpg, latest_1.jpg, etc.
        stem = p.stem  # e.g. "latest_0"
        try:
            display_id = int(stem.split("_", 1)[1])
            numbered.append((display_id, p))
        except (ValueError, IndexError):
            continue
    if numbered:
        numbered.sort(key=lambda x: x[0])
        return numbered
    # Backward compat: single latest.jpg
    if SNAPSHOT_IMAGE.exists():
        return [(0, SNAPSHOT_IMAGE)]
    return []


def _run_ocr_with_boxes(image_path: str) -> list[tuple[str, float, float, float, float]]:
    """Run macOS Vision OCR, returning text with normalized bounding boxes.

    Returns list of (text, x, y, w, h) where coordinates are normalized [0,1],
    bottom-left origin (as returned by Vision framework).
    """
    try:
        import objc
        from Quartz import (
            CGImageSourceCreateWithURL,
            CGImageSourceCreateImageAtIndex,
        )
        from Foundation import NSURL
        import Vision

        url = NSURL.fileURLWithPath_(image_path)
        source = CGImageSourceCreateWithURL(url, None)
        if source is None:
            return []
        cg_image = CGImageSourceCreateImageAtIndex(source, 0, None)
        if cg_image is None:
            return []

        results: list[tuple[str, float, float, float, float]] = []

        def completion(request, error):
            if error:
                return
            observations = request.results()
            if observations is None:
                return
            for obs in observations:
                candidates = obs.topCandidates_(1)
                if candidates and len(candidates) > 0:
                    text = candidates[0].string()
                    if text and text.strip():
                        bbox = obs.boundingBox()
                        results.append((
                            text.strip(),
                            bbox.origin.x,
                            bbox.origin.y,
                            bbox.size.width,
                            bbox.size.height,
                        ))

        request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(completion)
        request.setRecognitionLevel_(1)  # accurate
        request.setUsesLanguageCorrection_(True)

        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
            cg_image, None
        )
        handler.performRequests_error_([request], None)

        return results

    except ImportError:
        logger.debug("Vision/Quartz not available — trying tesseract fallback")
        return []
    except Exception:
        logger.debug("OCR failed", exc_info=True)
        return []


def _run_ocr(image_path: str) -> list[str]:
    """Run macOS Vision OCR on an image. Returns list of recognized text lines."""
    results = _run_ocr_with_boxes(image_path)
    if results:
        return [text for text, *_ in results]
    # Fall back to shortcuts-based OCR if Vision is unavailable
    return _run_ocr_fallback(image_path)


def _run_ocr_fallback(image_path: str) -> list[str]:
    """Fallback OCR using macOS shortcuts."""
    import subprocess
    try:
        result = subprocess.run(
            ["shortcuts", "run", "Extract Text from Image"],
            input=open(image_path, "rb").read(),
            capture_output=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.decode("utf-8", errors="replace").strip().split("\n")
    except Exception:
        pass
    return []


def _word_set(text: str) -> set[str]:
    """Normalize text to lowercase alphanumeric word set for OCR-noise-tolerant comparison."""
    return {w.lower() for w in re.findall(r'[a-zA-Z0-9]+', text) if len(w) > 1}


def _region_similarity(old_text: str, new_text: str) -> float:
    """Word-level Jaccard similarity between two region texts."""
    old_words = _word_set(old_text)
    new_words = _word_set(new_text)
    if not old_words and not new_words:
        return 1.0
    if not old_words or not new_words:
        return 0.0
    return len(old_words & new_words) / len(old_words | new_words)


def _classify_regions(
    prev_regions: list, curr_regions: list, input_state: str
) -> dict[str, str]:
    """Classify each current region as active/output/reference by comparing to previous frame.

    - active: user is typing AND this region changed → cursor is here
    - output: region changed but user isn't typing → automated output
    - reference: region unchanged → context material

    Threshold: 0.8 similarity (20%+ word change = "changed").
    """
    # Build lookup of previous regions by label
    prev_by_label: dict[str, str] = {}
    for r in prev_regions:
        prev_by_label[r.label] = r.text

    changes: dict[str, str] = {}
    is_typing = input_state == "typing"

    for r in curr_regions:
        prev_text = prev_by_label.get(r.label)
        if prev_text is None:
            # New region (app switch, layout change) → treat as output
            changes[r.label] = "output"
            continue

        sim = _region_similarity(prev_text, r.text)
        if sim >= 0.8:
            # Unchanged
            changes[r.label] = "reference"
        elif is_typing:
            # Changed + typing → active (cursor is here)
            changes[r.label] = "active"
        else:
            # Changed + not typing → automated output
            changes[r.label] = "output"

    return changes


# ---------------------------------------------------------------------------
# Screen buffer — holds raw OCR frames for direct LLM consumption
# ---------------------------------------------------------------------------

@dataclass
class ScreenFrame:
    """A single screen capture with raw OCR text."""
    ts: float
    app: str
    title: str  # window title from metadata
    text: str   # full OCR text, unfiltered
    text_hash: int  # for dedup
    regions: list | None = None  # list[ScreenRegion] if spatial OCR available
    input_state: str = "idle"                     # typing/reading/idle
    region_changes: dict[str, str] | None = None  # {label: "active"|"output"|"reference"}
    display_id: int = 0                           # which display this frame is from
    is_active_display: bool = True                # whether this is the display with the active app
    relevance: str = "work"                       # "work", "leisure", or "ambiguous"


# Module-level singleton buffer so all consumers share the same frames
_screen_buffer: ScreenBuffer | None = None


def get_screen_buffer() -> ScreenBuffer:
    """Get the shared screen buffer singleton."""
    global _screen_buffer
    if _screen_buffer is None:
        _screen_buffer = ScreenBuffer()
    return _screen_buffer


class ScreenBuffer:
    """Rolling buffer of raw screen captures.

    Stores the last N frames of full OCR text with timestamps and app info.
    Deduplicates consecutive identical screens. Provides frame selection
    for prompt generation — picks frames that show meaningful transitions.
    """

    MAX_FRAMES = 30  # ~5 min at 10s intervals

    def __init__(self) -> None:
        self._frames: list[ScreenFrame] = []

    def add(self, app: str, title: str, lines: list[str], regions: list | None = None, input_state: str = "idle", display_id: int = 0, is_active_display: bool = True, bundle_id: str = "") -> ScreenFrame | None:
        """Add a new OCR capture to the buffer. Returns frame if added, None if deduped."""
        text = "\n".join(lines)
        text_hash = hash(text[:500])  # hash on first 500 chars for speed

        # Skip if identical to last frame from the same display
        for prev in reversed(self._frames):
            if prev.display_id == display_id:
                if prev.text_hash == text_hash:
                    return None
                break

        region_changes = None
        if regions:
            # Find previous frame from same display for region comparison
            prev_frame = None
            for prev in reversed(self._frames):
                if prev.display_id == display_id and prev.regions:
                    prev_frame = prev
                    break
            if prev_frame:
                region_changes = _classify_regions(prev_frame.regions, regions, input_state)

        frame = ScreenFrame(
            ts=time.time(),
            app=app,
            title=title,
            text=text,
            text_hash=text_hash,
            regions=regions,
            input_state=input_state,
            region_changes=region_changes,
            display_id=display_id,
            is_active_display=is_active_display,
        )
        frame.relevance = classify_frame_relevance(app, title, text, bundle_id)
        self._frames.append(frame)

        # Trim to max size
        if len(self._frames) > self.MAX_FRAMES:
            self._frames = self._frames[-self.MAX_FRAMES:]

        return frame

    @property
    def frames(self) -> list[ScreenFrame]:
        return list(self._frames)

    @property
    def latest(self) -> ScreenFrame | None:
        return self._frames[-1] if self._frames else None

    def get_key_frames(self, max_frames: int = 5, max_age: float = 120.0) -> list[ScreenFrame]:
        """Select frames that show meaningful transitions across all displays.

        Picks frames where the screen content changed significantly,
        prioritizing recent frames and app switches. Ensures at least one
        frame from each recently-captured display is included.
        This is what gets fed to the LLM for prompt synthesis.
        """
        now = time.time()
        recent = [f for f in self._frames if now - f.ts < max_age and f.relevance != "leisure"]
        if not recent:
            return []

        # Group recent frames by display
        by_display: dict[int, list[ScreenFrame]] = {}
        for f in recent:
            by_display.setdefault(f.display_id, []).append(f)

        selected: list[ScreenFrame] = []

        # Ensure at least one frame from each display (latest from each)
        # Prioritize active display first
        display_ids = sorted(by_display.keys(), key=lambda d: not by_display[d][-1].is_active_display)
        for did in display_ids:
            frames = by_display[did]
            latest = frames[-1]
            if latest not in selected:
                selected.append(latest)

        # Fill remaining slots from the active display with transition frames
        active_frames = []
        for did in display_ids:
            if by_display[did][-1].is_active_display:
                active_frames = by_display[did]
                break
        if not active_frames:
            active_frames = recent

        for frame in reversed(active_frames[:-1]):
            if len(selected) >= max_frames:
                break

            # App switch = always interesting
            if frame.app != selected[-1].app:
                selected.append(frame)
                continue

            # Content change = interesting if substantial
            prev_preview = selected[-1].text[:200]
            this_preview = frame.text[:200]
            if this_preview != prev_preview:
                prev_words = set(prev_preview.split())
                this_words = set(this_preview.split())
                if prev_words and this_words:
                    overlap = len(prev_words & this_words) / max(len(prev_words), len(this_words))
                    if overlap < 0.7:  # >30% different
                        selected.append(frame)

        # Return in chronological order
        selected.sort(key=lambda f: (f.ts, f.display_id))
        return selected

    def format_for_llm(self, max_chars: int = 4500) -> str:
        """Format key frames as raw context for LLM consumption.

        This is the primary output — raw screen text with timestamps and
        app context from all displays. The LLM does all the interpretation.
        """
        frames = self.get_key_frames()
        if not frames:
            return ""

        now = time.time()
        parts = []
        chars_used = 0

        for frame in frames:
            age = int(now - frame.ts)
            if age < 5:
                when = "now"
            elif age < 60:
                when = f"{age}s ago"
            else:
                when = f"{age // 60}m ago"

            # Show input state and display info in header
            when_parts = [when]
            if frame.input_state and frame.input_state != "idle":
                when_parts.append(frame.input_state)
            # Tag display for multi-monitor setups
            display_tag = ""
            if len(set(f.display_id for f in frames)) > 1:
                display_label = "active" if frame.is_active_display else f"display {frame.display_id}"
                display_tag = f" ({display_label})"
            header = f"[{', '.join(when_parts)}] {frame.app}{display_tag}"
            if frame.title and frame.title != frame.app:
                header += f" — {frame.title}"

            # Truncate text to fit budget, keeping most meaningful lines
            remaining = max_chars - chars_used - len(header) - 20
            if remaining < 100:
                break

            # Ambiguous frames get reduced budget (leisure already filtered by get_key_frames)
            if frame.relevance == "ambiguous":
                remaining = remaining // 2

            # Use spatial regions if available, otherwise flat text
            if frame.regions:
                if frame.region_changes:
                    text = _format_regions_weighted(frame.regions, frame.region_changes, remaining)
                else:
                    from ..parsers.spatial import format_regions
                    text = format_regions(frame.regions)
                    text = text[:remaining] if len(text) > remaining else text
            else:
                text = _truncate_screen_text(frame.text, max_chars=remaining)
            section = f"{header}\n{text}"
            parts.append(section)
            chars_used += len(section)

        return "\n\n".join(parts)


def _truncate_screen_text(text: str, max_chars: int = 1500) -> str:
    """Truncate OCR text intelligently — keep meaningful lines, drop noise."""
    lines = text.split("\n")
    # Filter out very short lines (UI chrome, icons) and blank lines
    meaningful = [l for l in lines if len(l.strip()) > 3]
    if not meaningful:
        meaningful = lines

    result = []
    chars = 0
    for line in meaningful:
        if chars + len(line) + 1 > max_chars:
            break
        result.append(line)
        chars += len(line) + 1

    return "\n".join(result)


def _format_regions_weighted(
    regions: list, region_changes: dict[str, str], max_chars: int
) -> str:
    """Format regions with character budget weighted by classification.

    Budget allocation: active ~40%, output ~35%, reference ~25%.
    Redistributes budget when a classification has no regions.
    No explicit labels — just more text for the important region.
    """
    # Group regions by classification
    grouped: dict[str, list] = {"active": [], "output": [], "reference": []}
    for r in regions:
        cls = region_changes.get(r.label, "reference")
        grouped[cls].append(r)

    # Base budget ratios — balanced since screen content is primary signal
    ratios = {"active": 0.40, "output": 0.35, "reference": 0.25}

    # Redistribute budget from empty classifications
    empty_budget = sum(ratios[k] for k in ratios if not grouped[k])
    non_empty = [k for k in ratios if grouped[k]]
    if non_empty and empty_budget > 0:
        bonus = empty_budget / len(non_empty)
        ratios = {k: (ratios[k] + bonus if grouped[k] else 0) for k in ratios}

    # Allocate character budgets
    budgets = {k: int(max_chars * ratios[k]) for k in ratios}

    # Format each group
    parts: list[str] = []
    for cls in ("active", "output", "reference"):
        budget = budgets[cls]
        for r in grouped[cls]:
            text = r.text.strip()
            if not text:
                continue
            # Split budget evenly among regions in same classification
            n_in_class = len(grouped[cls])
            region_budget = budget // max(1, n_in_class)
            if len(text) > region_budget:
                text = text[:region_budget]
            parts.append(f"## {r.label}\n{text}")

    return "\n\n".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Regex-based extraction — fallback for rules-based prompt (no LLM)
# ---------------------------------------------------------------------------

def _extract_context_from_text(lines: list[str], app: str) -> dict[str, Any]:
    """Extract structured context from OCR text via regex.

    This is the FALLBACK path for when no LLM is configured.
    When an LLM is available, raw screen text goes directly to synthesis.
    """
    context: dict[str, Any] = {
        "app": app,
        "ocr_lines": len(lines),
    }

    full_text = "\n".join(lines)

    # Terminal commands
    terminal_patterns = [
        r"\$\s+(.+)", r"❯\s+(.+)", r"➜\s+(.+)", r">>>?\s+(.+)",
    ]
    commands = []
    for pattern in terminal_patterns:
        for m in re.finditer(pattern, full_text):
            cmd = m.group(1).strip()[:100]
            if cmd:
                commands.append(cmd)
    if commands:
        context["terminal_commands"] = commands[-5:]
        context["activity"] = "terminal"

    # AI agent indicators
    agent_indicators = {
        "claude": ["Claude", "claude >", "Thinking...", "Allow tool?"],
        "cursor": ["Cursor", "composer", "Generating"],
        "chatgpt": ["ChatGPT", "GPT-4"],
        "aider": ["aider", "Aider"],
        "codex": ["codex", "Codex"],
    }
    for agent, indicators in agent_indicators.items():
        for indicator in indicators:
            if indicator in full_text:
                context["agent_tool"] = agent
                break

    # File paths
    file_patterns = [
        r"([/~][\w./-]+\.\w{1,6})",
        r"(\w[\w/-]+\.(?:py|js|ts|tsx|rs|go|java|rb|swift|c|cpp|h))\b",
    ]
    files = []
    for pattern in file_patterns:
        for m in re.finditer(pattern, full_text):
            f = m.group(1)
            if len(f) > 3 and not f.startswith("http"):
                files.append(f)
    if files:
        context["files"] = list(dict.fromkeys(files))[:10]

    # Code editing indicators
    code_indicators = [
        r"\bdef\s+\w+", r"\bclass\s+\w+", r"\bfunction\s+\w+",
        r"\bimport\s+", r"\bfrom\s+\S+\s+import",
    ]
    if sum(1 for p in code_indicators if re.search(p, full_text)) >= 2:
        context["activity"] = "coding"

    # Errors
    error_patterns = [
        r"(?:Error|ERROR|error)[:\s](.{10,80})",
        r"(?:Traceback|Exception|Failed|FAILED)",
        r"(?:TypeError|ValueError|ImportError|SyntaxError|RuntimeError)",
    ]
    errors = []
    for pattern in error_patterns:
        for m in re.finditer(pattern, full_text):
            errors.append(m.group(0).strip()[:100])
    if errors:
        context["errors"] = errors[:3]
        context["has_errors"] = True

    # Summary
    summary_parts = []
    if context.get("agent_tool"):
        summary_parts.append(f"interacting with {context['agent_tool']}")
    if context.get("activity") == "terminal" and commands:
        summary_parts.append(f"running: {commands[-1]}")
    elif context.get("activity") == "coding" and files:
        summary_parts.append(f"editing {files[0]}")
    if context.get("has_errors"):
        summary_parts.append("(has errors)")
    if summary_parts:
        context["summary"] = "; ".join(summary_parts)

    # Text preview for rules-based fallback
    meaningful = [l for l in lines if len(l) > 3]
    context["text_preview"] = "\n".join(meaningful[:20])[:500]

    return context


# ---------------------------------------------------------------------------
# Observer — implements WorkflowObserver protocol + feeds screen buffer
# ---------------------------------------------------------------------------

class ScreenshotObserver:
    """Reads daemon screenshots, stores raw OCR in buffer, produces WorkflowUpdates."""

    def __init__(self, input_state_fn: Callable[[], str] | None = None) -> None:
        self._last_mtimes: dict[int, float] = {}  # per-display last mtime
        self._last_process_time: float = 0
        self._buffer = get_screen_buffer()
        self._input_state_fn = input_state_fn or (lambda: "idle")

    def _process_display(
        self, display_id: int, image_path: Path, app: str, title: str,
        input_state: str, is_active: bool, bundle_id: str = "",
    ) -> tuple[ScreenFrame | None, list]:
        """Process a single display snapshot. Returns (frame, blocks)."""
        ocr_results = _run_ocr_with_boxes(str(image_path))
        if not ocr_results:
            lines = _run_ocr_fallback(str(image_path))
            if not lines:
                return None, []
            frame = self._buffer.add(
                app, title, lines, input_state=input_state,
                display_id=display_id, is_active_display=is_active,
                bundle_id=bundle_id,
            )
            if frame:
                logger.info("Screen capture (flat) display %d: %s — %d lines", display_id, app, len(lines))
            return frame, []

        from ..parsers.spatial import TextBlock, cluster_into_regions
        lines = [text for text, *_ in ocr_results]
        blocks = [
            TextBlock(text=text, x=x, y=y, w=w, h=h)
            for text, x, y, w, h in ocr_results
        ]
        regions = cluster_into_regions(blocks, app=app)

        frame = self._buffer.add(
            app, title, lines, regions=regions, input_state=input_state,
            display_id=display_id, is_active_display=is_active,
            bundle_id=bundle_id,
        )
        if frame:
            logger.info("Screen capture display %d: %s — %d lines, %d regions", display_id, app, len(lines), len(regions))
        return frame, blocks

    def check(self) -> list:
        """WorkflowObserver protocol — capture new screenshots from all displays, return updates."""
        from .base import WorkflowUpdate

        now = time.time()
        if now - self._last_process_time < MIN_PROCESS_INTERVAL:
            return []

        display_snapshots = _find_display_snapshots()
        if not display_snapshots:
            return []

        # Read metadata (shared across displays)
        meta: dict = {}
        if SNAPSHOT_META.exists():
            try:
                meta = json.loads(SNAPSHOT_META.read_text())
            except Exception:
                pass

        # Determine which display is active
        active_display_id = meta.get("active_display", 0)
        # Per-display metadata (if provided)
        displays_meta: list[dict] = meta.get("displays", [])
        displays_meta_by_id: dict[int, dict] = {d.get("display_id", i): d for i, d in enumerate(displays_meta)}

        # Check if any display has new content
        any_new = False
        for display_id, path in display_snapshots:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > self._last_mtimes.get(display_id, 0):
                any_new = True
                break

        if not any_new:
            return []

        self._last_process_time = now
        input_state = self._input_state_fn()

        # Process each display
        all_frames: list[ScreenFrame] = []
        all_blocks: list = []
        primary_app = meta.get("app", "")
        primary_title = meta.get("title", "")

        for display_id, path in display_snapshots:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue

            if mtime <= self._last_mtimes.get(display_id, 0):
                continue  # this display hasn't changed

            self._last_mtimes[display_id] = mtime
            is_active = (display_id == active_display_id)

            # Use per-display metadata if available, else fall back to primary
            dmeta = displays_meta_by_id.get(display_id, {})
            app = dmeta.get("app", primary_app if is_active else "")
            title = dmeta.get("title", primary_title if is_active else "")
            bundle_id = dmeta.get("bundle_id", meta.get("bundle_id", ""))

            frame, blocks = self._process_display(
                display_id, path, app, title, input_state, is_active,
                bundle_id=bundle_id,
            )
            if frame:
                all_frames.append(frame)
                if blocks:
                    all_blocks.extend(blocks)

        if not all_frames:
            return []

        # Use the active display's frame for workflow updates
        active_frame = next((f for f in all_frames if f.is_active_display), all_frames[0])
        app = active_frame.app

        # Don't produce workflow updates for leisure content
        if active_frame.relevance == "leisure":
            logger.debug("Skipping workflow update — leisure frame: %s", app)
            return []

        # If messaging app, extract conversation context from OCR
        chat_context = None
        if all_blocks and app:
            from ..parsers.messaging_ocr import is_messaging_app, analyze_chat_screen
            if is_messaging_app(app):
                chat_context = analyze_chat_screen(all_blocks, app, active_frame.title)

        # Still produce WorkflowUpdates for workflow clustering
        keywords = []
        if app:
            keywords.append(app.lower().replace(" ", "_"))

        # Quick keyword extraction from screen text (just for clustering, not prompt)
        full_text = active_frame.text
        for pattern in [
            r"(\w[\w/-]+\.(?:py|js|ts|tsx|rs|go|java|rb|swift))\b",
        ]:
            for m in re.finditer(pattern, full_text):
                stem = Path(m.group(1)).stem
                if len(stem) > 2:
                    keywords.append(stem)
                    if len(keywords) >= 6:
                        break

        if not keywords:
            return []

        if chat_context:
            breadcrumb = chat_context.breadcrumb
            chat_keywords = chat_context.topic_keywords[:6]
            if chat_context.contact_name:
                chat_keywords.insert(0, chat_context.contact_name)
            keywords.extend(chat_keywords)
            stakeholders = [(n, "messaging") for n in chat_context.contacts_mentioned[:5]]
        else:
            breadcrumb = f"[screen] {app}" if app else "[screen]"
            stakeholders = []

        return [WorkflowUpdate(
            keywords=keywords[:8],
            breadcrumb=breadcrumb,
            tool=app,
            stakeholders=stakeholders,
        )]

    def get_latest_context(self) -> dict[str, Any] | None:
        """Get regex-extracted context from latest screenshot.

        This is the FALLBACK for rules-based prompt generation.
        When an LLM is available, use get_screen_buffer().format_for_llm() instead.
        """
        if not SNAPSHOT_IMAGE.exists():
            return None

        try:
            mtime = os.path.getmtime(SNAPSHOT_IMAGE)
            if time.time() - mtime > 30:
                return None
        except OSError:
            return None

        app = ""
        if SNAPSHOT_META.exists():
            try:
                meta = json.loads(SNAPSHOT_META.read_text())
                app = meta.get("app", "")
            except Exception:
                pass

        lines = _run_ocr(str(SNAPSHOT_IMAGE))
        if not lines:
            return None

        return _extract_context_from_text(lines, app)
