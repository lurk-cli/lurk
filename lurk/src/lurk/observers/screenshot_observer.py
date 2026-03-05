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
from typing import Any

logger = logging.getLogger("lurk.observers.screenshot")

SNAPSHOT_DIR = Path.home() / ".lurk" / "snapshots"
SNAPSHOT_IMAGE = SNAPSHOT_DIR / "latest.jpg"
SNAPSHOT_META = SNAPSHOT_DIR / "latest.json"

MIN_PROCESS_INTERVAL = 8.0  # seconds


def _run_ocr(image_path: str) -> list[str]:
    """Run macOS Vision OCR on an image. Returns list of recognized text lines."""
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

        results: list[str] = []

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
                        results.append(text.strip())

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
        return _run_ocr_fallback(image_path)
    except Exception:
        logger.debug("OCR failed", exc_info=True)
        return []


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

    def add(self, app: str, title: str, lines: list[str]) -> ScreenFrame | None:
        """Add a new OCR capture to the buffer. Returns frame if added, None if deduped."""
        text = "\n".join(lines)
        text_hash = hash(text[:500])  # hash on first 500 chars for speed

        # Skip if identical to last frame
        if self._frames and self._frames[-1].text_hash == text_hash:
            return None

        frame = ScreenFrame(
            ts=time.time(),
            app=app,
            title=title,
            text=text,
            text_hash=text_hash,
        )
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

    def get_key_frames(self, max_frames: int = 3, max_age: float = 120.0) -> list[ScreenFrame]:
        """Select frames that show meaningful transitions.

        Picks frames where the screen content changed significantly,
        prioritizing recent frames and app switches. This is what gets
        fed to the LLM for prompt synthesis.
        """
        now = time.time()
        recent = [f for f in self._frames if now - f.ts < max_age]
        if not recent:
            return []

        # Always include the latest frame
        selected = [recent[-1]]

        # Walk backwards, pick frames that differ from what we've selected
        for frame in reversed(recent[:-1]):
            if len(selected) >= max_frames:
                break

            # App switch = always interesting
            if frame.app != selected[-1].app:
                selected.append(frame)
                continue

            # Content change = interesting if substantial
            # Compare first 200 chars as a quick diff proxy
            prev_preview = selected[-1].text[:200]
            this_preview = frame.text[:200]
            if this_preview != prev_preview:
                # Count word-level differences
                prev_words = set(prev_preview.split())
                this_words = set(this_preview.split())
                if prev_words and this_words:
                    overlap = len(prev_words & this_words) / max(len(prev_words), len(this_words))
                    if overlap < 0.7:  # >30% different
                        selected.append(frame)

        # Return in chronological order
        selected.reverse()
        return selected

    def format_for_llm(self, max_chars: int = 3000) -> str:
        """Format key frames as raw context for LLM consumption.

        This is the primary output — raw screen text with timestamps and
        app context. The LLM does all the interpretation.
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

            header = f"[{when}] {frame.app}"
            if frame.title and frame.title != frame.app:
                header += f" — {frame.title}"

            # Truncate text to fit budget, keeping most meaningful lines
            remaining = max_chars - chars_used - len(header) - 20
            if remaining < 100:
                break

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

    def __init__(self) -> None:
        self._last_mtime: float = 0
        self._last_process_time: float = 0
        self._buffer = get_screen_buffer()

    def check(self) -> list:
        """WorkflowObserver protocol — capture new screenshot, return updates."""
        from .base import WorkflowUpdate

        now = time.time()
        if now - self._last_process_time < MIN_PROCESS_INTERVAL:
            return []

        if not SNAPSHOT_IMAGE.exists():
            return []

        try:
            mtime = os.path.getmtime(SNAPSHOT_IMAGE)
        except OSError:
            return []

        if mtime <= self._last_mtime:
            return []

        self._last_mtime = mtime
        self._last_process_time = now

        # Read metadata
        app = ""
        title = ""
        if SNAPSHOT_META.exists():
            try:
                meta = json.loads(SNAPSHOT_META.read_text())
                app = meta.get("app", "")
                title = meta.get("title", "")
            except Exception:
                pass

        # Run OCR
        lines = _run_ocr(str(SNAPSHOT_IMAGE))
        if not lines:
            return []

        # Store raw text in buffer (deduplicates internally)
        frame = self._buffer.add(app, title, lines)
        if frame is None:
            return []  # screen unchanged

        logger.info("Screen capture: %s — %d lines", app, len(lines))

        # Still produce WorkflowUpdates for workflow clustering
        # Use lightweight regex extraction just for keywords
        keywords = []
        if app:
            keywords.append(app.lower().replace(" ", "_"))

        # Quick keyword extraction from screen text (just for clustering, not prompt)
        full_text = frame.text
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

        breadcrumb = f"[screen] {app}" if app else "[screen]"

        return [WorkflowUpdate(
            keywords=keywords[:8],
            breadcrumb=breadcrumb,
            tool=app,
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
