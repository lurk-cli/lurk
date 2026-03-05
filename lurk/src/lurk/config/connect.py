"""Connect lurk to AI tools — auto-detect and configure MCP integrations."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("lurk.config")

CURSOR_MCP_CONFIG = Path.home() / ".cursor" / "mcp.json"
CODEX_MCP_CONFIG = Path.home() / ".codex" / "mcp.json"

SUPPORTED_TOOLS = {
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "codex": "Codex",
}

MCP_ENTRY = {
    "command": "lurk",
    "args": ["serve-mcp"],
}


def detect_installed_tools() -> list[str]:
    """Detect which AI tools are installed on this machine."""
    found = []

    if shutil.which("claude"):
        found.append("claude-code")

    cursor_paths = [
        Path("/Applications/Cursor.app"),
        Path.home() / "Applications" / "Cursor.app",
    ]
    if any(p.exists() for p in cursor_paths):
        found.append("cursor")

    if shutil.which("codex"):
        found.append("codex")

    return found


def is_connected(tool: str) -> bool:
    """Check if lurk is already configured for a given tool."""
    if tool == "claude-code":
        return _check_claude_code_connected()
    elif tool == "cursor":
        return _check_json_mcp_connected(CURSOR_MCP_CONFIG)
    elif tool == "codex":
        return _check_json_mcp_connected(CODEX_MCP_CONFIG)
    return False


def connect_tool(tool: str) -> tuple[bool, str]:
    """Connect lurk to an AI tool.

    Returns (success, message).
    """
    if tool == "claude-code":
        return _connect_claude_code()
    elif tool == "cursor":
        return _connect_json_mcp(tool, CURSOR_MCP_CONFIG)
    elif tool == "codex":
        return _connect_json_mcp(tool, CODEX_MCP_CONFIG)
    else:
        return False, f"Unknown tool: {tool}. Supported: {', '.join(SUPPORTED_TOOLS)}"


def _check_claude_code_connected() -> bool:
    """Check if lurk MCP is registered in Claude Code."""
    try:
        result = subprocess.run(
            ["claude", "mcp", "list"],
            capture_output=True, text=True, timeout=10,
        )
        return "lurk" in result.stdout.lower()
    except Exception:
        return False


def _connect_claude_code() -> tuple[bool, str]:
    """Register lurk as an MCP server in Claude Code."""
    if not shutil.which("claude"):
        return False, "claude CLI not found in PATH"

    try:
        result = subprocess.run(
            ["claude", "mcp", "add", "lurk", "--", "lurk", "serve-mcp"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return True, "Registered lurk MCP server in Claude Code"
        else:
            err = result.stderr.strip() or result.stdout.strip()
            if "already exists" in err.lower():
                return True, "lurk already registered in Claude Code"
            return False, f"claude mcp add failed: {err}"
    except Exception as e:
        return False, f"Failed to run claude CLI: {e}"


def _check_json_mcp_connected(config_path: Path) -> bool:
    """Check if lurk is in a JSON MCP config file (Cursor, Codex)."""
    if not config_path.exists():
        return False
    try:
        data = json.loads(config_path.read_text())
        servers = data.get("mcpServers", data.get("servers", {}))
        return "lurk" in servers
    except Exception:
        return False


def _connect_json_mcp(tool: str, config_path: Path) -> tuple[bool, str]:
    """Add lurk to a JSON MCP config file (Cursor, Codex)."""
    tool_name = SUPPORTED_TOOLS.get(tool, tool)

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            data = json.loads(config_path.read_text())
        else:
            data = {}

        # Cursor uses "mcpServers", Codex uses "servers"
        if tool == "cursor":
            key = "mcpServers"
        else:
            key = "servers"

        if key not in data:
            data[key] = {}

        if "lurk" in data[key]:
            return True, f"lurk already configured in {tool_name}"

        data[key]["lurk"] = MCP_ENTRY
        config_path.write_text(json.dumps(data, indent=2) + "\n")
        return True, f"Added lurk to {tool_name} MCP config ({config_path})"

    except Exception as e:
        return False, f"Failed to update {tool_name} config: {e}"
