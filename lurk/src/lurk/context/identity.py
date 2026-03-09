"""Project identity cache — extracts project context from repo docs."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("lurk.context.identity")

_DOC_FILES = ("CLAUDE.md", "README.md", "README")


class ProjectIdentity:
    """Structured project context extracted from repo docs."""

    def __init__(
        self,
        summary: str,
        description: str = "",
        tech_stack: str = "",
        architecture: str = "",
    ) -> None:
        self.summary = summary          # one-liner: "macOS context broker for AI tools"
        self.description = description  # 2-4 sentence expanded description
        self.tech_stack = tech_stack    # "Swift daemon + Python engine, SQLite, MCP/HTTP"
        self.architecture = architecture  # brief architecture note if available


class ProjectIdentityCache:
    """Caches project context keyed by project name.

    Reads README.md / CLAUDE.md once per project and extracts a structured
    identity that can be used to generate rich cold-start prompts.
    """

    def __init__(self) -> None:
        self._cache: dict[str, ProjectIdentity] = {}

    def get(self, project: str) -> str | None:
        """Get the one-liner summary for backward compat."""
        identity = self._cache.get(project)
        return identity.summary if identity else None

    def get_full(self, project: str) -> ProjectIdentity | None:
        """Get the full structured identity."""
        return self._cache.get(project)

    def set(self, project: str, repo_path: str) -> None:
        if project in self._cache:
            return

        try:
            identity = self._extract(repo_path)
        except Exception:
            logger.debug("Failed to extract identity for %s at %s", project, repo_path, exc_info=True)
            identity = None

        if identity:
            self._cache[project] = identity

    def _extract(self, repo_path: str) -> ProjectIdentity | None:
        root = Path(repo_path)
        if not root.is_dir():
            return None

        for filename in _DOC_FILES:
            path = root / filename
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if not text.strip():
                    continue

                if filename == "CLAUDE.md":
                    return self._extract_from_claude_md(text)
                else:
                    return self._extract_from_readme(text)

        return None

    # Lines that describe the file itself, not the project
    _META_PATTERNS = [
        "this file provides",
        "this document",
        "instructions for",
        "guidance to",
        "when working with",
        "codebase instructions",
    ]

    def _extract_from_claude_md(self, text: str) -> ProjectIdentity | None:
        lines = text.splitlines()

        # Get the one-liner from "## What is" section
        summary = None
        description_lines: list[str] = []
        tech_stack = ""
        architecture = ""

        i = 0
        while i < len(lines):
            line = lines[i]

            # "## What is lurk" or similar
            if re.match(r"^##\s+[Ww]hat\s+is", line):
                para = self._collect_paragraph(lines, i + 1)
                if para and not self._is_meta_text(para):
                    summary = self._clean_summary(para)
                    description_lines.append(para)
                i += 1
                continue

            # First paragraph after title (fallback for summary)
            if not summary and line.startswith("# "):
                # May need to skip meta paragraphs ("This file provides guidance...")
                j = i + 1
                while j < len(lines):
                    para = self._collect_paragraph(lines, j)
                    if not para:
                        break
                    if not self._is_meta_text(para):
                        summary = self._clean_summary(para)
                        description_lines.append(para)
                        break
                    # Skip this meta paragraph, try the next one
                    j += len(para.split("."))  # rough skip
                    while j < len(lines) and lines[j].strip():
                        j += 1
                    j += 1  # skip blank line
                i += 1
                continue

            # Architecture section — grab a brief note
            if re.match(r"^##\s+[Aa]rchitecture", line):
                para = self._collect_paragraph(lines, i + 1)
                if para and len(para) < 400:
                    architecture = para
                i += 1
                continue

            # Build / tech stack section
            if re.match(r"^##\s+(?:Build|Tech|Stack|Setup|Install)", line):
                para = self._collect_paragraph(lines, i + 1)
                if para:
                    # Extract just the key tech mentions
                    tech_stack = self._extract_tech_mentions(para, lines[i + 1:i + 20])
                i += 1
                continue

            i += 1

        if not summary:
            return None

        # Build expanded description from collected paragraphs
        description = " ".join(description_lines[:3])
        if len(description) > 500:
            # Trim to first 2 sentences
            m = re.match(r"^(.{50,500}?[.!])\s", description)
            description = m.group(1) if m else description[:500]

        return ProjectIdentity(
            summary=summary,
            description=description,
            tech_stack=tech_stack,
            architecture=architecture,
        )

    def _extract_from_readme(self, text: str) -> ProjectIdentity | None:
        lines = text.splitlines()

        # Find title, then grab first paragraph as summary
        summary = None
        description_lines: list[str] = []

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Skip badges and HTML
            if stripped.startswith("![") or re.match(r"^<(a |img |p>|div)", stripped, re.I):
                continue

            if line.startswith("# "):
                para = self._collect_paragraph(lines, i + 1, skip_badges=True)
                if para:
                    summary = self._clean_summary(para)
                    description_lines.append(para)
                break

        if not summary:
            # No title found — try first paragraph
            para = self._collect_paragraph(lines, 0, skip_badges=True)
            if para:
                summary = self._clean_summary(para)
                description_lines.append(para)

        if not summary:
            return None

        # Try to grab one more paragraph for expanded description
        for i, line in enumerate(lines):
            if line.startswith("## ") and not re.match(r"^##\s+(?:Table of|Install|Setup|Badge)", line):
                para = self._collect_paragraph(lines, i + 1, skip_badges=True)
                if para and len(para) > 30:
                    description_lines.append(para)
                    break

        description = " ".join(description_lines[:3])
        if len(description) > 500:
            m = re.match(r"^(.{50,500}?[.!])\s", description)
            description = m.group(1) if m else description[:500]

        return ProjectIdentity(summary=summary, description=description)

    @classmethod
    def _is_meta_text(cls, text: str) -> bool:
        """Check if text describes the file itself rather than the project."""
        text_lower = text.lower()[:100]
        return any(p in text_lower for p in cls._META_PATTERNS)

    @staticmethod
    def _collect_paragraph(lines: list[str], start: int, skip_badges: bool = False) -> str:
        """Collect the first non-empty paragraph after start."""
        buf: list[str] = []
        for line in lines[start:]:
            stripped = line.strip()

            if skip_badges and (stripped.startswith("![") or re.match(r"^<(a |img |p>|div)", stripped, re.I)):
                continue
            if stripped.startswith("#"):
                if buf:
                    break
                continue
            if stripped.startswith("```"):
                if buf:
                    break
                continue
            if not stripped:
                if buf:
                    break
                continue

            buf.append(stripped)

        if not buf:
            return ""
        return " ".join(buf)

    @staticmethod
    def _clean_summary(text: str) -> str:
        """Clean a paragraph into a one-liner summary."""
        # Take first sentence if long
        if len(text) > 200:
            m = re.match(r"^(.{20,200}?[.!])\s", text)
            if m:
                text = m.group(1)
            else:
                text = text[:200]

        text = text.rstrip(".")

        # Strip leading articles for natural reading after project name
        text = re.sub(r"^[Aa]n?\s+", "", text)

        # Lowercase first char if not acronym
        if len(text) >= 2 and text[0].isupper() and text[1].islower():
            text = text[0].lower() + text[1:]

        return text

    @staticmethod
    def _extract_tech_mentions(para: str, extra_lines: list[str]) -> str:
        """Extract key technology mentions from build/setup text."""
        combined = para + " " + " ".join(l.strip() for l in extra_lines if l.strip() and not l.strip().startswith("#"))

        techs: list[str] = []
        _keywords = [
            "swift", "python", "rust", "go", "typescript", "javascript",
            "react", "vue", "svelte", "next.js", "node",
            "sqlite", "postgres", "redis", "mongodb",
            "docker", "kubernetes",
            "mcp", "http", "grpc", "rest",
            "ollama", "openai", "anthropic",
        ]
        combined_lower = combined.lower()
        for kw in _keywords:
            if kw in combined_lower:
                # Acronyms stay uppercase, regular words get capitalized
                _acronyms = {"mcp", "http", "grpc", "rest", "sql", "api"}
                techs.append(kw.upper() if kw in _acronyms else kw.capitalize())

        return ", ".join(techs[:6]) if techs else ""
