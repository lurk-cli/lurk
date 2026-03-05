"""Git watcher — captures the actual code that coding agents write.

The useful context isn't "commit message: fix auth" — it's the actual diff,
the functions that were added, the logic that changed. When you switch from
Claude Code to ChatGPT, you want ChatGPT to know WHAT was built, not just
that something was committed.

This observer polls git repos and captures:
- The actual diff content (what lines were added/removed)
- Per-file diffs so each changed file's content is preserved
- New file contents in full (when an agent creates a new file)

All of this feeds into workflows and the context prompt, so every AI tool
you switch to already knows what was just written.
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("lurk.observers.git")

# Caps to keep captures bounded
MAX_DIFF_CHARS = 8000       # total diff content per snapshot
MAX_FILE_DIFF_CHARS = 3000  # per-file diff cap
MAX_FILE_DIFFS = 15         # max files to capture diffs for
MAX_NEW_FILE_CHARS = 2000   # full content of newly created files


@dataclass
class FileDiff:
    """The actual diff content for a single file."""
    path: str
    status: str  # M (modified), A (added), D (deleted), R (renamed)
    additions: list[str] = field(default_factory=list)   # lines added (without +)
    deletions: list[str] = field(default_factory=list)    # lines removed (without -)
    diff_text: str = ""      # raw unified diff chunk for this file
    language: str = ""       # inferred from extension
    new_file_content: str = ""  # full content if this is a brand new file

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "status": self.status,
            "additions": self.additions[:50],
            "deletions": self.deletions[:50],
            "diff_text": self.diff_text[:MAX_FILE_DIFF_CHARS],
            "language": self.language,
            "is_new_file": bool(self.new_file_content),
        }


@dataclass
class CodeSnapshot:
    """A snapshot of what code was actually written/changed."""
    project: str
    repo_path: str
    timestamp: float
    branch: str
    file_diffs: list[FileDiff] = field(default_factory=list)
    full_diff: str = ""       # the complete unified diff
    change_type: str = "working"  # commit | working
    commit_hash: str | None = None

    @property
    def files_touched(self) -> list[str]:
        return [fd.path for fd in self.file_diffs]

    @property
    def total_additions(self) -> int:
        return sum(len(fd.additions) for fd in self.file_diffs)

    @property
    def total_deletions(self) -> int:
        return sum(len(fd.deletions) for fd in self.file_diffs)

    def summary_text(self) -> str:
        """Build a natural language summary focused on what was actually written."""
        parts = []
        for fd in self.file_diffs:
            short_path = fd.path.rsplit("/", 1)[-1]
            lang = f" ({fd.language})" if fd.language else ""

            if fd.status == "A" and fd.new_file_content:
                parts.append(f"Created {short_path}{lang}:\n{fd.new_file_content}")
            elif fd.additions:
                added_block = "\n".join(fd.additions[:30])
                if fd.deletions:
                    removed_block = "\n".join(fd.deletions[:15])
                    parts.append(f"Modified {short_path}{lang}:\n  Added:\n{_indent(added_block)}\n  Removed:\n{_indent(removed_block)}")
                else:
                    parts.append(f"Added to {short_path}{lang}:\n{_indent(added_block)}")
            elif fd.deletions:
                removed_block = "\n".join(fd.deletions[:20])
                parts.append(f"Removed from {short_path}{lang}:\n{_indent(removed_block)}")
            elif fd.status == "D":
                parts.append(f"Deleted {short_path}")

        return "\n\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "project": self.project,
            "repo_path": self.repo_path,
            "timestamp": self.timestamp,
            "branch": self.branch,
            "change_type": self.change_type,
            "commit_hash": self.commit_hash,
            "files": [fd.to_dict() for fd in self.file_diffs],
            "total_additions": self.total_additions,
            "total_deletions": self.total_deletions,
            "summary": self.summary_text()[:MAX_DIFF_CHARS],
            "full_diff": self.full_diff[:MAX_DIFF_CHARS],
        }


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.split("\n"))


_LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "tsx", ".jsx": "jsx", ".rs": "rust", ".go": "go",
    ".java": "java", ".rb": "ruby", ".swift": "swift",
    ".c": "c", ".cpp": "c++", ".h": "c", ".css": "css",
    ".html": "html", ".sql": "sql", ".sh": "shell",
    ".yaml": "yaml", ".yml": "yaml", ".json": "json",
    ".md": "markdown", ".toml": "toml",
}


def _guess_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    return _LANG_MAP.get(ext, "")


@dataclass
class RepoState:
    """Tracked state for a git repository."""
    path: str
    last_head: str | None = None
    last_diff_hash: str | None = None
    last_check: float = 0


class GitWatcher:
    """Watches git repositories and captures the actual code agents write.

    Not just file names and line counts — the real diff content, the functions
    added, the logic changed. This is what carries context when switching tools.
    """

    POLL_INTERVAL = 10  # seconds between checks
    MAX_REPOS = 10

    def __init__(self) -> None:
        self._repos: dict[str, RepoState] = {}
        self._recent_snapshots: list[CodeSnapshot] = []
        self._max_recent = 30

    def register_project(self, project_name: str, repo_path: str) -> None:
        """Register a project's git repo for watching."""
        path = str(Path(repo_path).resolve())
        if not Path(path, ".git").exists():
            p = Path(path)
            while p != p.parent:
                if (p / ".git").exists():
                    path = str(p)
                    break
                p = p.parent
            else:
                return

        if path not in self._repos:
            if len(self._repos) >= self.MAX_REPOS:
                oldest = min(self._repos.values(), key=lambda r: r.last_check)
                del self._repos[oldest.path]

            state = RepoState(path=path)
            state.last_head = self._git_cmd(path, ["rev-parse", "HEAD"])
            state.last_diff_hash = self._diff_fingerprint(path)
            self._repos[path] = state
            logger.info("Watching git repo: %s (%s)", project_name, path)

    def auto_discover_from_model(self, model: Any) -> None:
        """Auto-register repos from known projects."""
        projects = model.projects.to_dict()
        for name, info in projects.items():
            candidates = [
                Path.home() / name,
                Path.home() / "Documents" / name,
                Path.home() / "Projects" / name,
                Path.home() / "dev" / name,
                Path.home() / "code" / name,
                Path.home() / "src" / name,
                Path.home() / "repos" / name,
                Path.home() / "workspace" / name,
            ]
            for f in info.get("files", []):
                if "/" in f:
                    parts = f.split("/")
                    for i, part in enumerate(parts):
                        if part == name:
                            candidate = Path("/".join(parts[:i+1]))
                            if candidate.exists():
                                candidates.insert(0, candidate)
                            break
            for candidate in candidates:
                if candidate.exists() and (candidate / ".git").exists():
                    self.register_project(name, str(candidate))
                    break

    def register_from_enriched_event(self, event: dict) -> None:
        """Discover repo path from an enriched event's file field."""
        file_path = event.get("file", "")
        project = event.get("project", "")
        if not file_path or not project:
            return
        if file_path.startswith("/"):
            p = Path(file_path).parent
            while p != p.parent:
                if (p / ".git").exists():
                    self.register_project(project, str(p))
                    return
                p = p.parent

    def check_all(self) -> list[CodeSnapshot]:
        """Check all repos. Returns new snapshots of actual code changes."""
        now = time.time()
        snapshots = []

        for path, state in list(self._repos.items()):
            if now - state.last_check < self.POLL_INTERVAL:
                continue
            state.last_check = now

            try:
                snap = self._check_repo(path, state)
                if snap:
                    snapshots.append(snap)
            except Exception:
                logger.debug("Error checking repo %s", path, exc_info=True)

        for snap in snapshots:
            self._recent_snapshots.append(snap)
        if len(self._recent_snapshots) > self._max_recent:
            self._recent_snapshots = self._recent_snapshots[-self._max_recent:]

        return snapshots

    def get_recent_snapshots(self, project: str | None = None, limit: int = 5) -> list[CodeSnapshot]:
        snaps = self._recent_snapshots
        if project:
            snaps = [s for s in snaps if s.project == project]
        return snaps[-limit:]

    def check(self) -> list:
        """WorkflowObserver protocol — returns WorkflowUpdate objects."""
        from .base import WorkflowUpdate
        snapshots = self.check_all()
        updates = []
        for snap in snapshots:
            keywords = [snap.project]
            if snap.branch and snap.branch not in ("main", "master"):
                keywords.append(snap.branch)
            for fd in snap.file_diffs[:5]:
                stem = fd.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                if len(stem) > 2:
                    keywords.append(stem)

            files = [fd.path for fd in snap.file_diffs]
            for fd in snap.file_diffs[:5]:
                code_change = ""
                if fd.status == "A":
                    code_change = f"created {fd.path}"
                elif fd.additions:
                    code_change = f"modified {fd.path}"
                if code_change:
                    updates.append(WorkflowUpdate(
                        keywords=keywords,
                        code_change=code_change,
                        project=snap.project,
                        files=files,
                    ))
        return updates

    def build_change_context(self, project: str | None = None) -> str:
        """Build context showing the actual code that was written."""
        snaps = self.get_recent_snapshots(project=project, limit=5)
        if not snaps:
            return ""

        parts = []
        for snap in snaps:
            header = f"In {snap.project} (branch: {snap.branch}):"
            body = snap.summary_text()
            if body:
                parts.append(f"{header}\n{body}")

        return "\n\n---\n\n".join(parts)

    # ---------------------------------------------------------------
    # Internal
    # ---------------------------------------------------------------

    def _check_repo(self, path: str, state: RepoState) -> CodeSnapshot | None:
        """Check a repo and capture actual diff content."""
        project = Path(path).name
        snapshot = None

        # Check for new commits
        current_head = self._git_cmd(path, ["rev-parse", "HEAD"])
        if current_head and current_head != state.last_head and state.last_head:
            snapshot = self._capture_commit_diff(path, project, state.last_head, current_head)
            state.last_head = current_head
        elif current_head and not state.last_head:
            state.last_head = current_head

        # Check working tree
        fp = self._diff_fingerprint(path)
        if fp != state.last_diff_hash and fp != "clean":
            working_snap = self._capture_working_diff(path, project)
            if working_snap and working_snap.file_diffs:
                # Prefer working tree if it has content; otherwise use commit
                snapshot = working_snap
            state.last_diff_hash = fp
        elif fp == "clean" and fp != state.last_diff_hash:
            state.last_diff_hash = fp

        return snapshot

    def _capture_commit_diff(self, path: str, project: str, old_head: str, new_head: str) -> CodeSnapshot | None:
        """Capture the actual diff content of new commits."""
        branch = self._git_cmd(path, ["branch", "--show-current"]) or "unknown"

        # Get the unified diff
        raw_diff = self._git_cmd(path, [
            "diff", old_head, new_head,
            "--no-color", f"-M",  # detect renames
        ], timeout=10)

        if not raw_diff:
            return None

        file_diffs = self._parse_unified_diff(raw_diff, path)
        if not file_diffs:
            return None

        return CodeSnapshot(
            project=project,
            repo_path=path,
            timestamp=time.time(),
            branch=branch,
            file_diffs=file_diffs,
            full_diff=raw_diff[:MAX_DIFF_CHARS],
            change_type="commit",
            commit_hash=new_head,
        )

    def _capture_working_diff(self, path: str, project: str) -> CodeSnapshot | None:
        """Capture actual diff content of working tree changes."""
        branch = self._git_cmd(path, ["branch", "--show-current"]) or "unknown"

        # Get unified diff of all changes (staged + unstaged) vs HEAD
        raw_diff = self._git_cmd(path, [
            "diff", "HEAD", "--no-color", "-M",
        ], timeout=10)

        if not raw_diff:
            return None

        file_diffs = self._parse_unified_diff(raw_diff, path)
        if not file_diffs:
            return None

        # For brand new untracked files, capture their content too
        untracked = self._git_cmd(path, [
            "ls-files", "--others", "--exclude-standard",
        ])
        if untracked:
            for ufile in untracked.strip().split("\n"):
                ufile = ufile.strip()
                if not ufile:
                    continue
                # Skip binary-looking files
                ext = Path(ufile).suffix.lower()
                if ext in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                           ".ttf", ".eot", ".zip", ".tar", ".gz", ".bin", ".exe",
                           ".pyc", ".so", ".dylib", ".DS_Store"}:
                    continue
                # Already in diff? skip
                if any(fd.path == ufile for fd in file_diffs):
                    continue
                if len(file_diffs) >= MAX_FILE_DIFFS:
                    break
                try:
                    content = (Path(path) / ufile).read_text(errors="replace")[:MAX_NEW_FILE_CHARS]
                    if content.strip():
                        file_diffs.append(FileDiff(
                            path=ufile,
                            status="A",
                            additions=content.split("\n"),
                            language=_guess_language(ufile),
                            new_file_content=content,
                        ))
                except Exception:
                    pass

        return CodeSnapshot(
            project=project,
            repo_path=path,
            timestamp=time.time(),
            branch=branch,
            file_diffs=file_diffs,
            full_diff=raw_diff[:MAX_DIFF_CHARS],
            change_type="working",
        )

    def _parse_unified_diff(self, raw_diff: str, repo_path: str) -> list[FileDiff]:
        """Parse a unified diff into per-file FileDiff objects with actual content."""
        file_diffs: list[FileDiff] = []
        current_file: FileDiff | None = None
        chars_used = 0

        for line in raw_diff.split("\n"):
            if chars_used > MAX_DIFF_CHARS:
                break

            # New file header
            if line.startswith("diff --git"):
                if current_file:
                    file_diffs.append(current_file)
                if len(file_diffs) >= MAX_FILE_DIFFS:
                    break

                # Extract file path from "diff --git a/path b/path"
                match = re.match(r"diff --git a/(.+?) b/(.+)", line)
                path = match.group(2) if match else "unknown"
                current_file = FileDiff(
                    path=path,
                    status="M",
                    language=_guess_language(path),
                )
                continue

            if current_file is None:
                continue

            # Detect new/deleted file
            if line.startswith("new file"):
                current_file.status = "A"
            elif line.startswith("deleted file"):
                current_file.status = "D"
            elif line.startswith("rename from"):
                current_file.status = "R"
            # Skip diff metadata lines
            elif line.startswith("---") or line.startswith("+++") or line.startswith("index "):
                continue
            elif line.startswith("@@"):
                # Hunk header — include it in diff_text for context
                current_file.diff_text += line + "\n"
                chars_used += len(line)
            elif line.startswith("+") and not line.startswith("+++"):
                added_line = line[1:]  # strip the +
                current_file.additions.append(added_line)
                current_file.diff_text += line + "\n"
                chars_used += len(line)
            elif line.startswith("-") and not line.startswith("---"):
                removed_line = line[1:]  # strip the -
                current_file.deletions.append(removed_line)
                current_file.diff_text += line + "\n"
                chars_used += len(line)
            # Context lines (unchanged) — include a few for readability
            elif line.startswith(" "):
                current_file.diff_text += line + "\n"
                chars_used += len(line)

        if current_file:
            file_diffs.append(current_file)

        # For new files, capture their full content
        for fd in file_diffs:
            if fd.status == "A" and fd.additions and not fd.new_file_content:
                fd.new_file_content = "\n".join(fd.additions)[:MAX_NEW_FILE_CHARS]

        return file_diffs

    def _diff_fingerprint(self, path: str) -> str:
        """Lightweight fingerprint of working tree state."""
        stat = self._git_cmd(path, ["diff", "--stat", "HEAD"])
        staged = self._git_cmd(path, ["diff", "--stat", "--cached"])
        combined = (stat or "") + (staged or "")
        if combined.strip():
            return hashlib.md5(combined.encode()).hexdigest()
        return "clean"

    def _git_cmd(self, path: str, args: list[str], timeout: int = 5) -> str | None:
        """Run a git command and return stdout, or None on failure."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=path, capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None
