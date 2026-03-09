"""Cold-start prompt synthesis — generates copy-pasteable context for AI tools.

Takes workstream data (LLM-inferred coherent threads of work) and produces
prompts that let users paste context into claude.ai, gemini.com, etc. without
re-explaining what they're working on.

Two formats:
- Human: natural language for pasting into chat interfaces
- XML: structured output for MCP/tool consumption (Claude Code, Cursor)
- Fallback: simpler format when no workstreams exist yet
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..context.model import ContextModel
    from ..context.workstreams import Workstream


def format_cold_start_human(
    workstream: Workstream,
    model: ContextModel,
    secondary_workstreams: list[Workstream] | None = None,
) -> str:
    """Generate a README-style context prompt for pasting into AI chat.

    Reads like a project briefing — what the project is, what the user
    is doing in it, what changed recently. No raw screen dumps.
    """
    parts: list[str] = []

    # Paragraph 1: Project identity + what I'm doing
    parts.append(_build_context_paragraph(model, workstream))

    # Paragraph 2: Recent code changes (most concrete signal)
    changes = _build_changes_summary(model)
    if changes:
        parts.append(changes)

    # Paragraph 3: Key decisions + people (if meaningful)
    context_notes = []
    decisions = workstream.key_decisions[:3] if workstream.key_decisions else []
    if decisions:
        context_notes.append("Key decisions: " + "; ".join(decisions) + ".")
    if workstream.key_people:
        people = [p for p in workstream.key_people[:5] if p.lower() not in ("developer", "user", "general")]
        if people:
            context_notes.append(f"People involved: {', '.join(people)}.")
    if context_notes:
        parts.append(" ".join(context_notes))

    # Paragraph 4: Background threads (concise)
    if secondary_workstreams:
        active = [ws for ws in secondary_workstreams if ws.inferred_goal and ws.id != workstream.id]
        if active:
            goals = "; ".join(_normalize_goal(ws.inferred_goal) for ws in active[:2])
            parts.append(f"Also in the background: {goals}.")

    parts.append("[What I need help with: ]")
    return "\n\n".join(parts)


def format_cold_start_xml(
    workstream: Workstream,
    model: ContextModel,
    secondary_workstreams: list[Workstream] | None = None,
) -> str:
    """Generate XML-tagged output for MCP/tool consumption.

    Used by Claude Code, Cursor, and other tools that parse structured context.
    """
    lines: list[str] = []
    persona = workstream.persona or "general"

    lines.append("<user_context>")

    # Project identity — what this project IS
    project = (workstream.projects[0] if workstream.projects else None) or model.now.project
    if project:
        identity = model.project_identity.get(project) if hasattr(model, "project_identity") else None
        if identity:
            lines.append(f"  <project name=\"{_xml_escape(project)}\">{_xml_escape(identity)}</project>")
        else:
            lines.append(f"  <project name=\"{_xml_escape(project)}\"/>")

    lines.append("  <primary_workstream>")
    lines.append(f"    <goal>{_xml_escape(workstream.inferred_goal)}</goal>")
    lines.append(f"    <persona>{_xml_escape(persona)}</persona>")

    if workstream.current_state:
        lines.append(f"    <state>{_xml_escape(workstream.current_state)}</state>")

    # Decisions
    if workstream.key_decisions:
        lines.append("    <decisions>")
        for decision in workstream.key_decisions[:5]:
            lines.append(f"      <decision>{_xml_escape(decision)}</decision>")
        lines.append("    </decisions>")

    # Artifacts
    artifacts = workstream.primary_artifacts
    if artifacts:
        lines.append("    <artifacts>")
        for artifact in artifacts[:8]:
            lines.append(f'      <file status="in-progress">{_xml_escape(artifact)}</file>')
        lines.append("    </artifacts>")

    # People
    if workstream.key_people:
        lines.append("    <people>")
        for person in workstream.key_people[:5]:
            lines.append(f'      <person role="collaborator">{_xml_escape(person)}</person>')
        lines.append("    </people>")

    # Code context (developer persona)
    if persona in ("developer", "general"):
        code_lines = _build_code_context_xml(workstream, model)
        if code_lines:
            lines.append("    <code_context>")
            lines.extend(f"      {line}" for line in code_lines)
            lines.append("    </code_context>")

    # Recent code changes from git
    changes = _build_changes_summary(model)
    if changes:
        lines.append(f"    <recent_changes>{_xml_escape(changes)}</recent_changes>")

    # Communications
    if workstream.related_communications:
        lines.append("    <communications>")
        for comm in workstream.related_communications[:3]:
            channel = comm.get("channel", "")
            with_person = comm.get("with", "")
            summary = comm.get("summary", "")
            attrs = ""
            if channel:
                attrs += f' channel="{_xml_escape(channel)}"'
            if with_person:
                attrs += f' with="{_xml_escape(with_person)}"'
            lines.append(f"      <conversation{attrs}>")
            if summary:
                lines.append(f"        {_xml_escape(summary)}")
            lines.append("      </conversation>")
        lines.append("    </communications>")

    lines.append("  </primary_workstream>")

    # Secondary workstreams
    if secondary_workstreams:
        active_secondary = [
            ws for ws in secondary_workstreams
            if ws.inferred_goal and ws.id != workstream.id
        ]
        if active_secondary:
            lines.append("  <secondary_workstreams>")
            for ws in active_secondary[:2]:
                lines.append("    <workstream>")
                lines.append(f"      <goal>{_xml_escape(ws.inferred_goal)}</goal>")
                if ws.current_state:
                    lines.append(f"      <state>{_xml_escape(ws.current_state)}</state>")
                lines.append("    </workstream>")
            lines.append("  </secondary_workstreams>")

    # Session info
    session_lines = _build_session_info_xml(workstream, model)
    if session_lines:
        lines.append("  <session_info>")
        lines.extend(f"    {line}" for line in session_lines)
        lines.append("  </session_info>")

    lines.append("</user_context>")

    return "\n".join(lines)


def format_cold_start_fallback(model: ContextModel) -> str:
    """Generate a README-style prompt when no workstreams exist yet.

    Uses concrete artifacts (project README, git diffs, session data)
    rather than raw screen dumps. Reads like a project briefing.
    """
    parts: list[str] = []

    # Paragraph 1: Project identity + what I'm doing
    parts.append(_build_context_paragraph(model))

    # Paragraph 2: Recent code changes
    changes = _build_changes_summary(model)
    if changes:
        parts.append(changes)

    # Paragraph 3: Workflow/session context (decisions, focus blocks)
    context_notes = []
    workflow_context = _build_workflow_context(model)
    if workflow_context:
        context_notes.append(workflow_context)
    session = model.session
    session_context = _build_session_context(session, model.now)
    if session_context:
        context_notes.append(session_context)
    if context_notes:
        parts.append(" ".join(context_notes))

    # Brief activity trail (only if we have very little context)
    if len(parts) <= 1:
        narrative = session.narrative()
        if narrative and "screen_capture" not in narrative:
            parts.append(f"Recent activity: {narrative}.")

    parts.append("[What I need help with: ]")

    if len(parts) <= 2:
        return "I'm starting a new work session.\n\n[What I need help with: ]"

    return "\n\n".join(parts)


def _build_screen_context(max_chars: int = 1500, for_human: bool = True) -> str:
    """Get screen content from the OCR buffer.

    For human (copy-paste) output: clean prose summary, no region labels.
    For MCP output: full spatial regions with labels.
    """
    try:
        from ..observers.screenshot_observer import get_screen_buffer
        buf = get_screen_buffer()

        if for_human:
            # For copy-paste: extract just the meaningful text content
            return _format_screen_for_human(buf, max_chars)
        else:
            text = buf.format_for_llm(max_chars=max_chars)
            if not text or len(text.strip()) < 20:
                return ""
            return text
    except Exception:
        return ""


def _format_screen_for_human(buf, max_chars: int = 1500) -> str:
    """Format screen buffer as clean, meaningful content for human consumption.

    Strategy:
    1. Use frame title to describe what's open (most reliable signal).
    2. Pick the largest region by text length as "main content" — this is
       usually the editor pane, document body, or main browser content.
    3. Aggressively filter UI chrome (menus, status bars, sidebar labels).
    4. Skip tiny regions (<30 chars) — they're buttons, icons, labels.
    5. Return concise, readable output (10-20 meaningful lines per frame).
    """
    frames = buf.get_key_frames() if hasattr(buf, 'get_key_frames') else []
    if not frames:
        return ""

    parts: list[str] = []
    chars_used = 0

    for frame in frames[:3]:
        app = frame.app or "Unknown"
        title = frame.title or ""

        # Build a concise header from app + title
        header = _screen_header(app, title)

        # Extract meaningful content from regions (preferred) or flat text
        content = _extract_main_content(frame, app)
        if not content:
            continue

        remaining = max_chars - chars_used - len(header) - 10
        if remaining < 50:
            break
        if len(content) > remaining:
            content = content[:remaining].rsplit("\n", 1)[0] + "\n..."

        parts.append(f"{header}:\n{content}")
        chars_used += len(header) + len(content) + 10

    return "\n\n".join(parts) if parts else ""


def _screen_header(app: str, title: str) -> str:
    """Build a concise header like 'Code — scenario.md' from app and title."""
    if not title or title == app:
        return app

    # For VS Code / editors: extract just the filename from long titles
    # e.g. "scenario.md — lurk — Visual Studio Code" -> "scenario.md"
    _editor_apps = {"code", "visual studio code", "cursor", "sublime text",
                    "xcode", "intellij idea", "pycharm", "webstorm", "vim",
                    "neovim", "zed"}
    if app.lower() in _editor_apps:
        # Title format is usually "filename — project — App"
        parts = [p.strip() for p in title.split("—")]
        if not parts:
            parts = [p.strip() for p in title.split("-")]
        file_part = parts[0] if parts else title
        # Clean up common prefixes/suffixes
        for suffix in (" [Extension Development Host]", " (Workspace)"):
            file_part = file_part.replace(suffix, "")
        return f"{app} — {file_part}"

    # For browsers: show the page title
    _browser_apps = {"google chrome", "safari", "firefox", "arc", "brave",
                     "microsoft edge", "chromium", "opera", "vivaldi"}
    if app.lower() in _browser_apps:
        # Strip trailing " - Google Chrome" etc.
        for suffix in [f" - {app}", f" — {app}", f" - {app.title()}", f"- {app}"]:
            if title.lower().endswith(suffix.lower()):
                title = title[:len(title) - len(suffix)].strip()
                break
        if len(title) > 60:
            title = title[:57] + "..."
        return f"{app} — {title}"

    # For terminal: show the command/path from title
    _terminal_apps = {"terminal", "iterm2", "iterm", "warp", "alacritty", "kitty"}
    if app.lower() in _terminal_apps:
        return f"{app} — {title}"

    # Default: app — title (truncated)
    if len(title) > 60:
        title = title[:57] + "..."
    return f"{app} — {title}"


def _extract_main_content(frame, app: str) -> str:
    """Extract the most meaningful text content from a screen frame.

    Uses regions if available (picks largest non-chrome region),
    falls back to filtered flat text.
    """
    app_lower = app.lower()

    if frame.regions:
        return _extract_from_regions(frame.regions, app_lower)

    # Flat text fallback
    if frame.text:
        lines = frame.text.split("\n")
        filtered = [l.strip() for l in lines if _is_meaningful_line(l.strip(), app_lower)]
        return "\n".join(filtered[:20]) if filtered else ""

    return ""


def _extract_from_regions(regions: list, app_lower: str) -> str:
    """Pick the best region(s) and extract clean content.

    For editors: prioritize the largest region (usually center = editor).
    For browsers: prioritize the largest region (usually main content).
    Skip tiny regions (<30 chars) and filter UI chrome from all regions.
    """
    if not regions:
        return ""

    # Score and filter regions
    scored: list[tuple[float, object]] = []
    for region in regions:
        text = getattr(region, "text", "") or ""
        label = (getattr(region, "label", "") or "").lower()
        text_len = len(text.strip())

        # Skip tiny regions — almost always UI chrome
        if text_len < 30:
            continue

        # Score: base = text length (bigger region = more content)
        score = float(text_len)

        # Boost center/content regions
        if label in ("content", "center", "middle"):
            score *= 2.0
        # Slightly boost bottom (terminal/output — often useful)
        elif "bottom" in label:
            score *= 1.2
        # Penalize top (menu/tab bars), left (sidebar/tree)
        elif label in ("top", "top left", "top right"):
            score *= 0.3
        elif label in ("left", "top left"):
            score *= 0.5

        scored.append((score, region))

    if not scored:
        return ""

    # Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Take the top region (main content), optionally a secondary one
    output_lines: list[str] = []
    budget = 20  # max lines total

    seen: set[str] = set()
    for _score, region in scored[:2]:
        text = getattr(region, "text", "") or ""
        lines = text.split("\n")
        filtered = [l.strip() for l in lines if _is_meaningful_line(l.strip(), app_lower)]
        if not filtered:
            continue

        for line in filtered:
            if budget <= 0:
                break
            # Deduplicate (OCR often picks up the same text from overlapping regions)
            line_key = line.lower().strip()
            if line_key in seen:
                continue
            seen.add(line_key)
            output_lines.append(line)
            budget -= 1

        if budget <= 0:
            break

    return "\n".join(output_lines) if output_lines else ""


# ---------------------------------------------------------------------------
# UI chrome filtering
# ---------------------------------------------------------------------------

# Comprehensive list of UI chrome patterns to filter out.
# These appear in OCR output from menus, status bars, sidebars, and toolbars.
_CHROME_PATTERNS_EXACT: frozenset[str] = frozenset({
    # macOS menu bar items
    "file", "edit", "view", "go", "window", "help", "terminal",
    "selection", "run", "debug", "source control", "extensions",
    "code", "finder", "safari", "google chrome", "firefox",
    # VS Code specific
    "explorer", "search", "outline", "timeline", "problems",
    "output", "debug console", "ports", "comments", "testing",
    "source control", "run and debug", "remote explorer",
    "no problems have been detected", "no results found",
    "workspace trust", "accounts",
    # Browser chrome
    "bookmarks", "reading list", "downloads", "history",
    "new tab", "extensions", "settings", "more tools",
    # Terminal chrome
    "profiles", "shell", "edit", "view",
    # Finder
    "applications", "documents", "desktop", "recents", "airdrop",
    # Generic buttons/labels
    "ok", "cancel", "save", "close", "open", "apply", "done",
    "yes", "no", "next", "back", "retry", "skip",
    "outline", "breadcrumbs",
})

# Substring patterns — if any of these appear in a line, skip it.
_CHROME_SUBSTRINGS: list[str] = [
    # VS Code status bar / chrome
    "file edit selection",
    "file edit view",
    "problems output",
    "debug console",
    "esc to interrupt",
    "context left until",
    "prettier",
    "no results",
    "trust the authors",
    "restricted mode",
    "manage workspace trust",
    "notifications",
    "layout controls",
    "toggle primary",
    "toggle secondary",
    "toggle panel",
    "open editors",
    "do not disturb",
    # VS Code footer
    "spaces:",
    "utf-8",
    "ln ",
    "col ",
    "crlf",
    "select language mode",
    "go to line",
    "select encoding",
    "select eol",
    "indent using",
    # VS Code sidebar
    "open a folder",
    "no folder opened",
    "you have not yet opened",
    # macOS system chrome
    "battery",
    "control center",
    "stage manager",
    "notification center",
    # Browser address bar / chrome
    "type a url",
    "search or type",
    "search google",
    "search the web",
    "not secure",
    "connection is secure",
    "cookies and site data",
    "privacy report",
    # Tab indicators
    "loading...",
    "waiting for",
    # Terminal noise from OCR
    "running_",
    "coffdnand",
    "corthand",
    "bash corthand",
    "bash coffdnand",
    "(no output)",
    "ictrl+",
    "ctrl+o to expand",
    # VS Code tips and UI
    "tip: open the command palette",
    "shell command: install",
    "accept edits on",
    "shift+tab to cycle",
    "cmd+shift+p",
    "hatching_",
    # Claude Code / AI assistant UI
    "tokens remaining",
    "k tokens)",
    "context left",
    "do you want to proceed",
    "esc to cancel",
    "tab to amend",
    "command contains newlines",
    "separate multiple commands",
    "reinstall, start, and test",
    # Google auth / security prompts
    "your device will ask",
    "fingerprint, face",
    "screen lock",
    # More Claude Code / tool UI
    "ctrl+b to run",
    "run in background",
    "cooking_",
    "ollama",
    "llama3",
    "timeout im)",
    "added i line",
    "added 1 line",
    "removed i line",
    "removed 1 line",
    "updatel ",
    "update|",
    "+f rom",
    "+from",
    "f rom pathllb",
    "f rom typing",
    "this command requires approval",
    "command contains output redirection",
    # Zoom / video
    "mute", "unmute", "share screen", "start video", "stop video",
    "participants", "leave meeting",
    # Generic toolbar patterns
    "toolbar",
    "sidebar",
    "status bar",
    "menu bar",
]

# Regex patterns for lines that are almost certainly UI chrome
_CHROME_REGEXES: list[re.Pattern] = [
    re.compile(r"^[\W\s]{1,5}$"),                      # just symbols/punctuation
    re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+ \d+"),       # "Mon Mar 9" date in menu bar
    re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?$"),  # time only "3:48PM"
    re.compile(r"^[OoQq•\s\.\,\-\|]{2,}$"),             # OCR noise: dots, bullets, pipes
    re.compile(r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s"),   # day-of-week in menu bar
    re.compile(r"^\d+\s*(?:items?|files?|results?)"),    # "3 items" count labels
    re.compile(r"^[\u2318\u2325\u21E7\u2303\u238B\u21A9\u2190-\u21FF\s]+$"),  # keyboard shortcut symbols
    re.compile(r"^[A-Z]$"),                              # single capital letter
    re.compile(r"^[\d\s\.\,]+$"),                        # just numbers/whitespace
    re.compile(r"^\d+%$"),                               # percentage "85%"
    re.compile(r"^(?:Copy|Paste|Cut|Undo|Redo|Select All|Find|Replace)$", re.IGNORECASE),
    re.compile(r"^\*?\s*\w+_?\s+\d+[mhsd]\b"),       # "* Hatching_ 115m 16s" process status
    re.compile(r"^[tl]\s*\d+\.?\d*k?\s*tokens?\)?"),   # "t 10.4k tokens)" OCR garble of token counter
    re.compile(r"^L \(timeout"),                        # "L (timeout 1m)" — tool timeout display
    re.compile(r"^\d{1,5}\s*[+\-]?\s*$"),               # "713 +" — editor line numbers with diff markers
    re.compile(r"^\d{1,5}\s*\|"),                        # "42 |" — editor line number gutters
    re.compile(r"^\)\s*[lI\d]\.\s*(Yes|No)\b"),          # ") l. Yes" — dialog choice buttons
    re.compile(r"^>\s*[A-Z]+$"),                         # "> OUTLINE" — sidebar section headers
    re.compile(r"^output redirection"),                  # security warning fragments
]


def _is_meaningful_line(line: str, app_lower: str = "") -> bool:
    """Return True if a line contains meaningful content, not UI chrome."""
    if not line:
        return False

    # Too short — almost always a button or label
    if len(line) <= 3:
        return False

    # Exact match against known chrome labels
    if line.lower() in _CHROME_PATTERNS_EXACT:
        return False

    # Substring match
    line_lower = line.lower()
    for pattern in _CHROME_SUBSTRINGS:
        if pattern in line_lower:
            return False

    # Regex match
    for regex in _CHROME_REGEXES:
        if regex.match(line):
            return False

    # App-specific filters — apply VS Code filters broadly since OCR can
    # capture VS Code content while another app is active
    if _is_vscode_chrome(line, line_lower):
        return False

    if _is_browser_chrome(line, line_lower):
        return False

    return True


def _is_vscode_chrome(line: str, line_lower: str) -> bool:
    """Filter VS Code-specific UI chrome that gets picked up by OCR."""
    vscode_chrome = [
        "explorer", "search", "source control", "run and debug",
        "extensions", "testing", "outline", "timeline",
        "open editors", "no folder opened", "git graph",
        "breadcrumb", "minimap", "chat",
    ]
    if line_lower in vscode_chrome:
        return True

    # Tab bar — "scenario.md U X", "JS onboard.js" etc.
    if line_lower.endswith((" u", " m", " x", " u x", " m x")) and len(line) < 40:
        return True

    # File tree items: bare filenames (short, has extension, no other context)
    # e.g. "pyproject.toml", "package.json", ".gitignore", "install.sh"
    stripped = line.strip()
    if len(stripped) < 40 and not stripped.startswith(("#", "//", "/*", "-", "*", "def ", "class ", "import ", "from ")):
        # Check if it looks like a bare filename
        import os.path
        _, ext = os.path.splitext(stripped.split()[-1] if stripped.split() else "")
        # It's a bare filename if: has extension, and the whole line is basically just the name
        # (allow for leading tree indicators like "v ", "> ", "I I ")
        clean = stripped.lstrip("vV>• \t|I¥").strip()
        if ext and len(clean.split()) <= 2 and len(clean) < 35:
            return True

    # VS Code file tree chevrons/indent guides: "v lurk", "> src", "I I package.json"
    # OCR produces various prefix symbols: v, >, •, •>, ¥, *, O, I I
    if stripped.startswith(("v ", "> ", "I I ", "¥ ", "• ", "•> ", "V ", "* ", "O ")):
        rest = re.sub(r"^[vV>•¥OI\*\s\|]+", "", stripped).strip()
        if len(rest) < 40 and len(rest.split()) <= 3:
            return True

    # Git diff markers in sidebar: "+2 line5", "M file.py"
    if re.match(r"^\+\d+\s+line", stripped, re.IGNORECASE):
        return True

    # Bare directory/file names in sidebar (no context, just a name)
    # "node modules", "node_modules", "src", "dist", "build", etc.
    if len(stripped) < 30 and len(stripped.split()) <= 2:
        # If it looks like a path component (alphanumeric + dots/underscores/hyphens, no spaces or just 2 words)
        if re.match(r"^[\w\-\.]+(\s[\w\-\.]+)?$", stripped) and not any(c in stripped for c in "(){}[];:=<>\"'#/\\"):
            # But don't filter lines that look like actual code/content
            # Code usually has operators, punctuation, or is part of a sentence
            return True

    # Short labels that are likely sidebar/panel names
    if len(stripped) < 15 and stripped.replace(" ", "").isalpha() and stripped[0].isupper():
        # Single capitalized word or two-word label: "Chat", "Terminal", "Problems"
        if len(stripped.split()) <= 2:
            return True

    return False


def _is_browser_chrome(line: str, line_lower: str) -> bool:
    """Filter browser-specific UI chrome."""
    browser_chrome = [
        "new tab", "bookmarks bar", "other bookmarks",
        "reading list", "most visited",
    ]
    if line_lower in browser_chrome:
        return True

    # Navigation buttons picked up by OCR
    if line_lower in ("<", ">", "x", "...", "+"):
        return True

    # Tab/favicon row — short sequences of single characters
    # e.g. "4 C G" from tab favicons
    if len(line) < 10 and all(len(w) <= 2 for w in line.split()):
        return True

    # Raw URLs in address bar (long, noisy — not useful context)
    if re.match(r"^https?://\S{60,}", line) or re.match(r"^\w+\.\w+\.\w+/\S{40,}", line):
        return True

    return False


def _build_context_paragraph(model: ContextModel, workstream=None) -> str:
    """Build the opening paragraph: project identity + expanded description + current task.

    Reads like the opening of a README — what this project is, what it does,
    and what the user is currently working on within it.
    """
    now = model.now
    session = model.session

    # Resolve project name
    project = now.project
    if not project and session.projects_touched:
        project = session.projects_touched[-1]
    if not project and workstream and workstream.projects:
        project = workstream.projects[0]
    if not project and hasattr(model, "workflows"):
        for wf in model.workflows.list_workflows():
            if wf.projects:
                project = wf.projects[-1]
                break

    # Get full project identity
    full_identity = model.project_identity.get_full(project) if project else None

    sentences: list[str] = []

    if project and full_identity:
        # Rich opener: project name + what it is + expanded description
        sentences.append(f"I'm working on {project}, {full_identity.summary}.")
        if full_identity.description:
            # Add expanded description, stripping any overlap with the summary
            desc = full_identity.description
            summary_lower = full_identity.summary.lower()
            # Find where the summary text ends in the description
            idx = desc.lower().find(summary_lower)
            if idx >= 0:
                desc = desc[idx + len(summary_lower):].lstrip(". ")
            if desc and len(desc) > 20:
                sentences.append(desc if desc.endswith(".") else desc + ".")
        if full_identity.tech_stack:
            sentences.append(f"Tech stack: {full_identity.tech_stack}.")
    elif project:
        branch = now.branch
        if branch:
            sentences.append(f"I'm working on the {project} project (branch: {branch}).")
        else:
            sentences.append(f"I'm working on the {project} project.")
    elif workstream and workstream.inferred_goal:
        goal = _normalize_goal(workstream.inferred_goal)
        sentences.append(f"I'm working on {goal}.")
    elif now.file:
        lang = f" ({now.language})" if now.language else ""
        sentences.append(f"I'm working on {now.file}{lang}.")
    else:
        current = _describe_current_activity(now)
        if current:
            sentences.append(f"I'm currently {current}.")
        else:
            sentences.append("I'm starting a new work session.")

    # Append current task
    task = _infer_current_task(model, workstream)
    if task:
        sentences.append(task)

    return " ".join(sentences)


def _infer_current_task(model: ContextModel, workstream=None) -> str | None:
    """Infer what the user is currently doing from concrete signals.

    Priority: git diffs > workstream state > screen content > activity labels.
    """
    now = model.now

    # 1. Recent code changes — most concrete signal
    snaps = model.recent_code_snapshots[-3:] if model.recent_code_snapshots else []
    if snaps:
        files = set()
        for snap in snaps:
            for fd in snap.file_diffs:
                fname = fd.path.rsplit("/", 1)[-1] if "/" in fd.path else fd.path
                files.add(fname)
        if files:
            files_str = ", ".join(sorted(files)[:5])
            return f"Currently modifying {files_str}."

    # 2. Workstream current_state (if it's not just repeating the app name)
    if workstream and workstream.current_state:
        state = workstream.current_state
        # Filter out generic states like "Currently coding in Google Chrome"
        state_lower = state.lower()
        if not any(generic in state_lower for generic in ("coding in", "browsing", "using")):
            return state + ("." if not state.endswith(".") else "")

    # 3. File + activity from snapshot
    if now.file and now.project:
        return f"Currently editing {now.file}."
    elif now.file:
        return f"Currently working on {now.file}."

    return None


def _build_changes_summary(model: ContextModel) -> str | None:
    """Build a summary of recent code changes from git snapshots."""
    snaps = model.recent_code_snapshots[-5:] if model.recent_code_snapshots else []
    if not snaps:
        return None

    file_stats: dict[str, tuple[int, int, str]] = {}  # fname -> (adds, dels, status)
    for snap in snaps:
        for fd in snap.file_diffs:
            fname = fd.path.rsplit("/", 1)[-1] if "/" in fd.path else fd.path
            adds = len(fd.additions) if hasattr(fd, "additions") else 0
            dels = len(fd.deletions) if hasattr(fd, "deletions") else 0
            status = getattr(fd, "status", "M")
            if fname in file_stats:
                prev_a, prev_d, _ = file_stats[fname]
                file_stats[fname] = (prev_a + adds, prev_d + dels, status)
            else:
                file_stats[fname] = (adds, dels, status)

    if not file_stats:
        return None

    parts = []
    for fname, (adds, dels, status) in sorted(file_stats.items(), key=lambda x: x[1][0] + x[1][1], reverse=True)[:6]:
        if status == "A":
            parts.append(f"created {fname}")
        elif adds or dels:
            parts.append(f"{fname} (+{adds}/-{dels})")
        else:
            parts.append(fname)

    return "Recent changes: " + ", ".join(parts) + "."


def _build_workflow_context(model: ContextModel) -> str:
    """Extract meaningful context from active workflows."""
    if not hasattr(model, "workflows"):
        return ""

    active_wf = model.workflows.get_active_workflow()
    if not active_wf:
        return ""

    parts: list[str] = []

    # Key decisions
    if active_wf.key_decisions:
        decisions_str = "; ".join(active_wf.key_decisions[-3:])
        parts.append(f"Key decisions: {decisions_str}.")

    # Agent contributions
    for tool, summary in (active_wf.agent_contributions or {}).items():
        parts.append(f"{tool}: {summary}")

    # Code changes
    if active_wf.code_changes:
        changes_str = "; ".join(active_wf.code_changes[-3:])
        parts.append(f"Recent changes: {changes_str}.")

    # Documents
    if active_wf.documents:
        for name, desc in list(active_wf.documents.items())[-2:]:
            if desc:
                parts.append(f'Working with "{name}" ({desc}).')

    return " ".join(parts) if parts else ""


def _build_session_context(session, now) -> str:
    """Build context from the work session — focus blocks, research, projects."""
    parts: list[str] = []

    # Projects touched this session
    if session.projects_touched and len(session.projects_touched) > 1:
        parts.append(f"Projects this session: {', '.join(session.projects_touched[-4:])}.")

    # Focus blocks — show where deep work happened
    if session.focus_blocks:
        recent_block = session.focus_blocks[-1]
        if recent_block.duration_seconds > 300:  # >5 min
            mins = int(recent_block.duration_seconds / 60)
            block_desc = f"{recent_block.project}" if recent_block.project else recent_block.activity
            if recent_block.files_touched:
                files_str = ", ".join(recent_block.files_touched[-3:])
                parts.append(f"Deep focus ({mins}m) on {block_desc}: {files_str}.")
            else:
                parts.append(f"Deep focus ({mins}m) on {block_desc}.")

    # Research trail
    if session.research_trail:
        topics = [r.topic for r in session.research_trail[-3:] if r.topic]
        if topics:
            parts.append(f"Researched: {', '.join(topics)}.")

    # Tickets
    if session.tickets_worked:
        parts.append(f"Tickets: {', '.join(session.tickets_worked[-3:])}.")

    return " ".join(parts) if parts else ""


def _build_agent_context(model: ContextModel) -> str:
    """Context from AI agent sessions."""
    agents = model.agents
    if not agents.sessions:
        return ""

    _names = {
        "claude_code": "Claude Code", "cursor_agent": "Cursor",
        "codex": "Codex", "chatgpt": "ChatGPT", "copilot": "Copilot",
        "aider": "Aider", "goose": "Goose",
    }

    active_parts: list[str] = []
    for s in agents.sessions.values():
        name = _names.get(s.tool, s.tool)
        mins = round(s.duration_seconds / 60)
        proj = f" on {s.project}" if s.project else ""
        task = ""
        if hasattr(s, "last_prompt") and s.last_prompt:
            task = f': "{s.last_prompt[:80]}"'
        active_parts.append(f"{name}{proj} ({mins}m){task}")

    if not active_parts:
        return ""
    return "AI agents: " + "; ".join(active_parts) + "."


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_context_bullets(
    workstream: Workstream,
    model: ContextModel,
    persona: str,
) -> list[str]:
    """Build key context bullet points adapted to persona."""
    bullets: list[str] = []

    # Decisions are relevant for all personas
    for decision in workstream.key_decisions[:3]:
        bullets.append(decision)

    if persona == "developer":
        # Files, branches, recent code changes
        if workstream.primary_artifacts:
            files_str = ", ".join(workstream.primary_artifacts[:4])
            bullets.append(f"Key files: {files_str}")
        if workstream.git_branches:
            bullets.append(f"Branch: {', '.join(workstream.git_branches[:2])}")
        # Recent code changes from the workflow
        wf = _get_linked_workflow(workstream, model)
        if wf and wf.code_changes:
            for change in wf.code_changes[-2:]:
                bullets.append(change)

    elif persona == "pm":
        # Stakeholders, timeline, artifacts
        if workstream.primary_artifacts:
            docs_str = ", ".join(workstream.primary_artifacts[:3])
            bullets.append(f"Key documents: {docs_str}")
        if workstream.supporting_research:
            topics = [r.get("topic", "") for r in workstream.supporting_research[-2:] if r.get("topic")]
            if topics:
                bullets.append(f"Research: {', '.join(topics)}")

    elif persona == "designer":
        # Design files, feedback
        if workstream.primary_artifacts:
            files_str = ", ".join(workstream.primary_artifacts[:3])
            bullets.append(f"Design files: {files_str}")

    elif persona == "marketer":
        # Campaign details, channels
        if workstream.primary_artifacts:
            assets_str = ", ".join(workstream.primary_artifacts[:3])
            bullets.append(f"Assets: {assets_str}")

    else:
        # General — balanced mix
        if workstream.primary_artifacts:
            files_str = ", ".join(workstream.primary_artifacts[:3])
            bullets.append(f"Key files: {files_str}")
        if workstream.git_branches:
            bullets.append(f"Branch: {', '.join(workstream.git_branches[:2])}")

    # Projects (all personas)
    if workstream.projects:
        projects_str = ", ".join(workstream.projects[:3])
        bullets.append(f"Project: {projects_str}")

    # Tools used (all personas, only if interesting)
    if len(workstream.tools_used) > 2:
        tools_str = ", ".join(workstream.tools_used[:5])
        bullets.append(f"Using: {tools_str}")

    return bullets


def _format_communications(workstream: Workstream) -> str:
    """Format recent communications into natural language."""
    if not workstream.related_communications:
        return ""

    comms = workstream.related_communications[-2:]
    parts: list[str] = []
    for comm in comms:
        summary = comm.get("summary", "")
        with_person = comm.get("with", "")
        channel = comm.get("channel", "")
        if summary:
            prefix = ""
            if with_person:
                prefix = f"Discussed with {with_person}: "
            elif channel:
                prefix = f"In {channel}: "
            parts.append(f"{prefix}{summary}")

    if not parts:
        return ""
    return "Recent conversations: " + "; ".join(parts) + "."


def _build_code_context_xml(workstream: Workstream, model: ContextModel) -> list[str]:
    """Build XML lines for code context."""
    lines: list[str] = []

    if workstream.git_branches:
        lines.append(f"<branch>{_xml_escape(workstream.git_branches[0])}</branch>")

    # Get code changes from linked workflow
    wf = _get_linked_workflow(workstream, model)
    if wf and wf.code_changes:
        recent_changes = "; ".join(wf.code_changes[-3:])
        lines.append(f"<recent_changes>{_xml_escape(recent_changes)}</recent_changes>")

    # Current branch/project from snapshot
    now = model.now
    if not workstream.git_branches and now.branch:
        lines.append(f"<branch>{_xml_escape(now.branch)}</branch>")

    return lines


def _build_session_info_xml(workstream: Workstream, model: ContextModel) -> list[str]:
    """Build XML lines for session info."""
    lines: list[str] = []
    session = model.session

    duration_min = int(session.duration_seconds / 60)
    if duration_min > 0:
        lines.append(f"<active_duration>{duration_min} minutes</active_duration>")

    tools = workstream.tools_used or model.now.tools_active
    if tools:
        tools_str = ", ".join(tools[:6])
        lines.append(f"<tools_used>{_xml_escape(tools_str)}</tools_used>")

    return lines


def _get_linked_workflow(workstream: Workstream, model: ContextModel):
    """Get the workflow linked to this workstream, if any."""
    if not workstream.workflow_ids:
        # Fall back to the active workflow
        if hasattr(model, "workflows"):
            return model.workflows.get_active_workflow()
        return None
    # Try to find the most recent linked workflow
    if hasattr(model, "workflows"):
        for wid in reversed(workstream.workflow_ids):
            wf = model.workflows.get_workflow(wid)
            if wf:
                return wf
    return None


def _describe_current_activity(now) -> str:
    """Describe what the user is currently doing from the snapshot."""
    doc = getattr(now, "document_name", None)
    file = getattr(now, "file", None)
    project = getattr(now, "project", None)
    app = now.app or ""

    if file and project:
        lang = f" ({now.language})" if getattr(now, "language", None) else ""
        return f"working on {file} in the {project} project{lang}"

    if doc and app:
        return f"working on \"{doc}\" in {app}"

    if project and app:
        return f"working in {app} on the {project} project"

    if app:
        activity = getattr(now, "activity", "")
        _internal = {"unknown", "idle", "screen_capture", "general", "system", "media_playback", "recording", ""}
        if activity and activity not in _internal:
            return f"{activity} in {app}"
        return f"working in {app}"

    return ""


def _describe_project_info(now) -> str:
    """Describe project/branch info from the snapshot."""
    project = getattr(now, "project", None)
    branch = getattr(now, "branch", None)

    if project and branch:
        return f"Project: {project} (branch: {branch})"
    if project:
        return f"Project: {project}"
    return ""


def _normalize_goal(goal: str) -> str:
    """Convert LLM-generated goals to first-person, natural phrasing.

    Strips "The user is..." prefix so it reads naturally after "I'm working on".
    """
    if not goal:
        return "something"
    g = goal.strip().rstrip(".")
    # Strip third-person prefixes
    for prefix in ("The user is ", "the user is ", "User is ", "They are "):
        if g.startswith(prefix):
            g = g[len(prefix):]
            break
    # Strip "working on" since we prepend "I'm working on"
    for prefix in ("working on ", "Working on "):
        if g.startswith(prefix):
            g = g[len(prefix):]
            break
    # Lowercase first letter if it's not an acronym or proper noun
    if g and g[0].isupper() and (len(g) < 2 or g[1].islower()):
        g = g[0].lower() + g[1:]
    return g


def _xml_escape(text: str) -> str:
    """Escape special characters for XML output."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
