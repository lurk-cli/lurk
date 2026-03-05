"""Tool context file writer — writes structured context to tool-specific files.

Manages a clearly-delimited section within each file using <!-- lurk:START --> / <!-- lurk:END -->
markers. Content outside the markers is never modified.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from .adapters import claude_md, cursorrules, lurk_context

if TYPE_CHECKING:
    from ..context.model import ContextModel

logger = logging.getLogger("lurk.writer")

START_MARKER = "<!-- lurk:START — Auto-generated context. Do not edit between markers. -->"
END_MARKER = "<!-- lurk:END -->"

# Project root markers
PROJECT_MARKERS = [
    ".git", "package.json", "Cargo.toml", "pyproject.toml",
    "go.mod", "pom.xml", "build.gradle", "Makefile",
    "CMakeLists.txt", ".project", "Gemfile", "mix.exs",
]

# Common workspace directories to search
WORKSPACE_DIRS = [
    Path.home(),
    Path.home() / "code",
    Path.home() / "projects",
    Path.home() / "Developer",
    Path.home() / "dev",
    Path.home() / "src",
    Path.home() / "repos",
    Path.home() / "workspace",
    Path.home() / "Documents",
]


@dataclass
class WriterTarget:
    """A file target for context writing."""
    filename: str
    render: Callable
    enabled: bool = True


@dataclass
class ContextFileWriter:
    """Writes structured context to tool-specific files."""

    targets: list[WriterTarget] = field(default_factory=list)
    _last_hashes: dict[str, str] = field(default_factory=dict)
    _last_project_root: Path | None = None

    def __init__(self, enabled_targets: list[str] | None = None) -> None:
        """Initialize with enabled targets.

        Args:
            enabled_targets: List of target names to enable.
                Options: "claude_md", "cursorrules", "lurk_context"
                Default: ["lurk_context"]
        """
        if enabled_targets is None:
            enabled_targets = ["lurk_context"]

        self.targets = [
            WriterTarget("CLAUDE.md", claude_md.render, "claude_md" in enabled_targets),
            WriterTarget(".cursorrules", cursorrules.render, "cursorrules" in enabled_targets),
            WriterTarget(".lurk-context.md", lurk_context.render, "lurk_context" in enabled_targets),
        ]
        self._last_hashes = {}
        self._last_project_root = None

    def write(self, model: ContextModel) -> list[str]:
        """Write context to all enabled targets. Returns list of files written."""
        project_root = self._detect_project_root(model)
        if not project_root:
            return []

        written = []
        for target in self.targets:
            if not target.enabled:
                continue

            content = target.render(model)
            file_path = project_root / target.filename

            # Content hash check — skip if unchanged
            content_hash = hashlib.md5(content.encode()).hexdigest()
            hash_key = str(file_path)
            if self._last_hashes.get(hash_key) == content_hash:
                continue

            # Write the file
            if self._write_with_markers(file_path, content):
                self._last_hashes[hash_key] = content_hash
                written.append(str(file_path))
                logger.debug("Wrote context to %s", file_path)

        # Manage .gitignore for .lurk-context.md
        if any(t.filename == ".lurk-context.md" and t.enabled for t in self.targets):
            self._ensure_gitignore(project_root)

        return written

    def _detect_project_root(self, model: ContextModel) -> Path | None:
        """Detect the project root from the current context."""
        project_name = model.now.project
        if not project_name:
            return self._last_project_root

        # Try to find the project directory
        for workspace in WORKSPACE_DIRS:
            if not workspace.exists():
                continue

            candidate = workspace / project_name
            if candidate.is_dir() and self._is_project_root(candidate):
                self._last_project_root = candidate
                return candidate

            # Search one level deep
            try:
                for subdir in workspace.iterdir():
                    if not subdir.is_dir():
                        continue
                    candidate = subdir / project_name
                    if candidate.is_dir() and self._is_project_root(candidate):
                        self._last_project_root = candidate
                        return candidate
                    # Also check if the subdir itself matches
                    if subdir.name == project_name and self._is_project_root(subdir):
                        self._last_project_root = subdir
                        return subdir
            except PermissionError:
                continue

        return self._last_project_root

    @staticmethod
    def _is_project_root(path: Path) -> bool:
        """Check if a directory is a project root."""
        return any((path / marker).exists() for marker in PROJECT_MARKERS)

    @staticmethod
    def _write_with_markers(file_path: Path, content: str) -> bool:
        """Write content between lurk markers in a file. Atomic write."""
        try:
            marked_content = f"{START_MARKER}\n\n{content}\n{END_MARKER}"

            if file_path.exists():
                existing = file_path.read_text()

                # Replace content between existing markers
                start_idx = existing.find(START_MARKER)
                end_idx = existing.find(END_MARKER)

                if start_idx >= 0 and end_idx >= 0:
                    end_idx += len(END_MARKER)
                    new_content = existing[:start_idx] + marked_content + existing[end_idx:]
                else:
                    # Append markers to end of file
                    separator = "\n\n" if existing and not existing.endswith("\n\n") else "\n" if existing and not existing.endswith("\n") else ""
                    new_content = existing + separator + marked_content + "\n"
            else:
                new_content = marked_content + "\n"

            # Atomic write: write to temp file, then rename
            dir_path = file_path.parent
            dir_path.mkdir(parents=True, exist_ok=True)

            fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
            try:
                os.write(fd, new_content.encode())
                os.close(fd)
                os.rename(tmp_path, str(file_path))
                return True
            except Exception:
                os.close(fd) if not os.get_inheritable(fd) else None
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

        except Exception:
            logger.exception("Failed to write %s", file_path)
            return False

    @staticmethod
    def _ensure_gitignore(project_root: Path) -> None:
        """Ensure .lurk-context.md is in .gitignore."""
        gitignore = project_root / ".gitignore"
        entry = ".lurk-context.md"

        if gitignore.exists():
            content = gitignore.read_text()
            if entry in content:
                return
            # Append
            separator = "\n" if content and not content.endswith("\n") else ""
            with open(gitignore, "a") as f:
                f.write(f"{separator}\n# lurk context broker\n{entry}\n")
        else:
            # Only create .gitignore if the project has .git
            if (project_root / ".git").exists():
                gitignore.write_text(f"# lurk context broker\n{entry}\n")


class WriterLoop:
    """Runs the file writer on a timer, only writing when context changes."""

    def __init__(
        self,
        model: ContextModel,
        enabled_targets: list[str] | None = None,
        interval: float = 30.0,
    ) -> None:
        self.model = model
        self.writer = ContextFileWriter(enabled_targets)
        self.interval = interval

    def run(self) -> None:
        """Run the writer loop."""
        logger.info("File writer loop started (every %.0fs)", self.interval)
        while True:
            try:
                written = self.writer.write(self.model)
                if written:
                    logger.info("Updated: %s", ", ".join(written))
            except Exception:
                logger.exception("Error in file writer loop")
            time.sleep(self.interval)
