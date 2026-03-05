"""Screenshot observer — reads screen captures and extracts context via OCR.

The daemon captures screenshots of the active window every ~10 seconds.
This observer reads those screenshots, runs OCR to extract visible text,
and produces WorkflowUpdate objects that feed into the enrichment pipeline.

This solves the fundamental limitation of title-based observation:
terminal apps, embedded editors, split views, and browser tabs all
look the same from the window title, but the actual screen content
tells you exactly what the user is doing.

Uses macOS Vision framework (VNRecognizeTextRequest) for fast, local OCR.
No external dependencies, no cloud APIs, no data leaving the machine.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("lurk.observers.screenshot")

SNAPSHOT_DIR = Path.home() / ".lurk" / "snapshots"
SNAPSHOT_IMAGE = SNAPSHOT_DIR / "latest.jpg"
SNAPSHOT_META = SNAPSHOT_DIR / "latest.json"

# Throttle: don't process more often than this
MIN_PROCESS_INTERVAL = 8.0  # seconds

# Cache: skip if image hasn't changed
_last_mtime: float = 0
_last_process_time: float = 0
_last_ocr_hash: int = 0


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

        # Load image
        url = NSURL.fileURLWithPath_(image_path)
        source = CGImageSourceCreateWithURL(url, None)
        if source is None:
            return []
        cg_image = CGImageSourceCreateImageAtIndex(source, 0, None)
        if cg_image is None:
            return []

        # Create text recognition request
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
    """Fallback OCR using subprocess (screencapture -t on macOS)."""
    import subprocess
    try:
        # Try using macOS shortcuts/automator for text extraction
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


def _extract_context_from_text(lines: list[str], app: str) -> dict[str, Any]:
    """Extract structured context from OCR text lines.

    Looks for signals like:
    - File paths and names
    - Terminal prompts and commands
    - Code patterns (function defs, imports)
    - AI agent indicators (Claude, ChatGPT prompts)
    - Error messages
    - Project/directory names
    """
    context: dict[str, Any] = {
        "app": app,
        "ocr_lines": len(lines),
    }

    full_text = "\n".join(lines)

    # Detect terminal/CLI activity
    terminal_patterns = [
        r"\$\s+(.+)",           # shell prompt
        r"❯\s+(.+)",           # starship/custom prompt
        r"➜\s+(.+)",           # oh-my-zsh
        r">>>?\s+(.+)",        # python REPL
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

    # Detect Claude Code / AI agent activity
    agent_indicators = {
        "claude": ["Claude", "claude >", "Thinking...", "Allow tool?", "Co-Authored-By: Claude"],
        "cursor": ["Cursor", "composer", "Generating"],
        "chatgpt": ["ChatGPT", "GPT-4"],
        "aider": ["aider", "Aider"],
        "codex": ["codex", "Codex"],
    }
    for agent, indicators in agent_indicators.items():
        for indicator in indicators:
            if indicator in full_text:
                context["agent_tool"] = agent
                context["agent_active"] = True
                break

    # Detect file paths
    file_patterns = [
        r"([/~][\w./-]+\.\w{1,6})",                     # unix paths
        r"(\w[\w/-]+\.(?:py|js|ts|tsx|rs|go|java|rb|swift|c|cpp|h))\b",  # source files
    ]
    files = []
    for pattern in file_patterns:
        for m in re.finditer(pattern, full_text):
            f = m.group(1)
            if len(f) > 3 and not f.startswith("http"):
                files.append(f)
    if files:
        context["files"] = list(dict.fromkeys(files))[:10]

    # Detect project/directory from prompts
    dir_patterns = [
        r"(?:in|at)\s+(\S+/\S+)",         # "in project/dir"
        r"~/([A-Za-z][\w-]+/[\w-]+)",      # ~/Documents/lurk
        r"\(([a-zA-Z][\w-]+)\)",           # (main), (feat/auth)
    ]
    for pattern in dir_patterns:
        for m in re.finditer(pattern, full_text):
            val = m.group(1)
            if len(val) > 2:
                context.setdefault("project_hints", []).append(val)

    # Detect code editing
    code_indicators = [
        r"\bdef\s+\w+",
        r"\bclass\s+\w+",
        r"\bfunction\s+\w+",
        r"\bimport\s+",
        r"\bfrom\s+\S+\s+import",
        r"\bconst\s+\w+\s*=",
        r"\blet\s+\w+\s*=",
    ]
    code_count = sum(1 for p in code_indicators if re.search(p, full_text))
    if code_count >= 2:
        context["activity"] = "coding"

    # Detect errors
    error_patterns = [
        r"(?:Error|ERROR|error)[:\s](.{10,80})",
        r"(?:Traceback|Exception|Failed|FAILED)",
        r"(?:TypeError|ValueError|ImportError|SyntaxError|RuntimeError)",
    ]
    errors = []
    for pattern in error_patterns:
        for m in re.finditer(pattern, full_text):
            err = m.group(0).strip()[:100]
            if err:
                errors.append(err)
    if errors:
        context["errors"] = errors[:3]
        context["has_errors"] = True

    # Detect git activity
    git_patterns = [
        r"(?:On branch|HEAD detached at)\s+(\S+)",
        r"(?:commit|merge|rebase|cherry-pick)\s",
        r"(?:git\s+(?:push|pull|commit|merge|rebase|diff|log|status))",
    ]
    for pattern in git_patterns:
        m = re.search(pattern, full_text)
        if m:
            context["git_active"] = True
            if m.lastindex:
                context["branch"] = m.group(1)
            break

    # Build a natural language summary of what's on screen
    summary_parts = []
    if context.get("agent_tool"):
        summary_parts.append(f"interacting with {context['agent_tool']}")
    if context.get("activity") == "terminal":
        if commands:
            summary_parts.append(f"running: {commands[-1]}")
    elif context.get("activity") == "coding":
        if files:
            summary_parts.append(f"editing {files[0]}")
    if context.get("has_errors"):
        summary_parts.append("(has errors)")

    if summary_parts:
        context["summary"] = "; ".join(summary_parts)

    # Include a text preview (first ~500 chars of meaningful text)
    meaningful = [l for l in lines if len(l) > 3 and not l.startswith("●")]
    context["text_preview"] = "\n".join(meaningful[:20])[:500]

    return context


class ScreenshotObserver:
    """Reads daemon screenshots, runs OCR, produces WorkflowUpdates.

    Implements the WorkflowObserver protocol.
    """

    def __init__(self) -> None:
        self._last_mtime: float = 0
        self._last_process_time: float = 0
        self._last_text_hash: int = 0

    def check(self) -> list:
        """WorkflowObserver protocol — returns WorkflowUpdate objects."""
        from .base import WorkflowUpdate

        now = time.time()
        if now - self._last_process_time < MIN_PROCESS_INTERVAL:
            return []

        # Check if a new screenshot exists
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
        if SNAPSHOT_META.exists():
            try:
                meta = json.loads(SNAPSHOT_META.read_text())
                app = meta.get("app", "")
            except Exception:
                pass

        # Run OCR
        lines = _run_ocr(str(SNAPSHOT_IMAGE))
        if not lines:
            return []

        # Quick dedup — skip if text hasn't changed
        text_hash = hash(tuple(lines[:10]))
        if text_hash == self._last_text_hash:
            return []
        self._last_text_hash = text_hash

        # Extract structured context
        context = _extract_context_from_text(lines, app)

        logger.info(
            "Screenshot OCR: %s — %d lines, activity=%s, agent=%s",
            app, len(lines),
            context.get("activity", "?"),
            context.get("agent_tool", "none"),
        )

        # Build keywords from extracted context
        keywords = []
        if app:
            keywords.append(app.lower().replace(" ", "_"))
        for f in context.get("files", [])[:3]:
            stem = Path(f).stem
            if len(stem) > 2:
                keywords.append(stem)
        if context.get("agent_tool"):
            keywords.append(context["agent_tool"])
        for hint in context.get("project_hints", [])[:2]:
            keywords.append(hint.split("/")[-1])

        if not keywords:
            return []

        # Build breadcrumb
        breadcrumb = ""
        if context.get("summary"):
            breadcrumb = f"[screen] {context['summary']}"
        elif app:
            breadcrumb = f"[screen] active in {app}"

        update = WorkflowUpdate(
            keywords=keywords,
            breadcrumb=breadcrumb,
            tool=app,
        )

        # If we detected an agent, add it as a contribution hint
        if context.get("agent_tool") and context.get("terminal_commands"):
            update.agent_contribution = (
                context["agent_tool"],
                f"active session — {context['terminal_commands'][-1][:80]}"
            )

        if context.get("files"):
            update.files = context["files"][:5]

        return [update]

    def get_latest_context(self) -> dict[str, Any] | None:
        """Get the latest screenshot analysis without producing updates.

        Useful for enriching other events with screen content.
        """
        if not SNAPSHOT_IMAGE.exists():
            return None

        try:
            mtime = os.path.getmtime(SNAPSHOT_IMAGE)
            if time.time() - mtime > 30:  # stale
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
