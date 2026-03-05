"""Parser for terminal apps."""

from __future__ import annotations

import re

from .base import AppParser, ParsedContext


class TerminalParser(AppParser):
    name = "terminal"
    app_names = ["Terminal", "iTerm2", "iTerm", "Warp", "Alacritty", "kitty", "WezTerm"]
    bundle_ids = [
        "com.apple.Terminal",
        "com.googlecode.iterm2",
        "dev.warp.Warp-Stable",
        "io.alacritty",
        "net.kovidgoyal.kitty",
        "com.github.wez.wezterm",
    ]

    # Common patterns in terminal titles
    _path_re = re.compile(r"[~/][^\s:]+")
    _ssh_re = re.compile(r"ssh\s+\S+")
    _process_re = re.compile(r"(?:python|node|npm|cargo|go|make|docker|git|vim|nvim|nano)\b")

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        ctx = ParsedContext(
            app=app,
            parser_name=self.name,
            activity="coding",
            sub_activity="terminal",
        )

        if not title:
            return ctx

        # Try to extract working directory
        path_match = self._path_re.search(title)
        if path_match:
            path = path_match.group()
            # Extract project name from path
            parts = path.rstrip("/").split("/")
            if parts:
                ctx.project = parts[-1]

        # Detect SSH sessions
        if self._ssh_re.search(title):
            ctx.sub_activity = "ssh"

        # Detect running processes for intent
        process_match = self._process_re.search(title)
        if process_match:
            process = process_match.group()
            if process in ("vim", "nvim", "nano"):
                ctx.sub_activity = "editing"
            elif process in ("python", "node", "cargo", "go"):
                ctx.sub_activity = "running"
            elif process == "git":
                ctx.sub_activity = "version_control"
            elif process == "docker":
                ctx.sub_activity = "devops"
            elif process in ("npm", "make"):
                ctx.sub_activity = "building"

        return ctx
