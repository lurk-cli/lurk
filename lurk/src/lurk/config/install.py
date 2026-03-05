"""Install and auto-start system — launchd plist management for macOS.

Handles:
- Creating/removing launchd plist for daemon auto-start
- First-run permission checks (Accessibility)
- Install/uninstall workflows
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("lurk.config")

PLIST_LABEL = "com.lurk.daemon"
LAUNCHD_DIR = Path.home() / "Library" / "LaunchAgents"
PLIST_PATH = LAUNCHD_DIR / f"{PLIST_LABEL}.plist"
LURK_DIR = Path.home() / ".lurk"
DEFAULT_CONFIG_PATH = LURK_DIR / "config.yaml"


def get_plist_content(daemon_path: str, python_path: str | None = None) -> str:
    """Generate the launchd plist XML content."""
    log_path = LURK_DIR / "daemon.log"
    err_path = LURK_DIR / "daemon.err"

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{daemon_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{err_path}</string>
    <key>ProcessType</key>
    <string>Background</string>
    <key>LowPriorityBackgroundIO</key>
    <true/>
    <key>Nice</key>
    <integer>10</integer>
</dict>
</plist>
"""


def install_launchd(daemon_path: str) -> bool:
    """Install the launchd plist for auto-start on login.

    Returns True if installed successfully.
    """
    try:
        LURK_DIR.mkdir(parents=True, exist_ok=True)
        LAUNCHD_DIR.mkdir(parents=True, exist_ok=True)

        plist_content = get_plist_content(daemon_path)
        PLIST_PATH.write_text(plist_content)
        logger.info("Wrote launchd plist to %s", PLIST_PATH)

        # Load the plist
        result = subprocess.run(
            ["launchctl", "load", str(PLIST_PATH)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            logger.warning("launchctl load warning: %s", result.stderr)

        return True
    except Exception:
        logger.exception("Failed to install launchd plist")
        return False


def uninstall_launchd() -> bool:
    """Remove the launchd plist and stop the daemon.

    Returns True if uninstalled successfully.
    """
    try:
        if PLIST_PATH.exists():
            # Unload first
            subprocess.run(
                ["launchctl", "unload", str(PLIST_PATH)],
                capture_output=True, text=True,
            )
            PLIST_PATH.unlink()
            logger.info("Removed launchd plist")
        return True
    except Exception:
        logger.exception("Failed to uninstall launchd plist")
        return False


def is_installed() -> bool:
    """Check if the launchd plist is installed."""
    return PLIST_PATH.exists()


def check_accessibility() -> bool:
    """Check if Accessibility permission is granted.

    Returns True if the app has Accessibility access.
    """
    try:
        # Use tccutil to check (macOS doesn't have a CLI for this)
        # Instead, we try to check via the daemon's AXIsProcessTrusted
        # For now, check if the TCC database has an entry
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of first process'],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def find_daemon_binary() -> str | None:
    """Find the lurk-daemon binary."""
    # Check common locations
    candidates = [
        # Development build
        Path(__file__).parent.parent.parent.parent.parent / "daemon" / ".build" / "debug" / "lurk-daemon",
        # Release build
        Path(__file__).parent.parent.parent.parent.parent / "daemon" / ".build" / "release" / "lurk-daemon",
        # Homebrew
        Path("/usr/local/bin/lurk-daemon"),
        Path("/opt/homebrew/bin/lurk-daemon"),
        # User local
        Path.home() / ".local" / "bin" / "lurk-daemon",
    ]

    for path in candidates:
        if path.exists() and os.access(str(path), os.X_OK):
            return str(path)

    # Check PATH
    found = shutil.which("lurk-daemon")
    return found


def full_install(daemon_path: str | None = None) -> dict[str, bool]:
    """Run the full installation workflow.

    Returns dict of step → success.
    """
    results: dict[str, bool] = {}

    # Find daemon
    if daemon_path is None:
        daemon_path = find_daemon_binary()
    results["daemon_found"] = daemon_path is not None

    if daemon_path is None:
        return results

    # Create ~/.lurk directory
    LURK_DIR.mkdir(parents=True, exist_ok=True)
    results["lurk_dir"] = True

    # Install launchd plist
    results["launchd"] = install_launchd(daemon_path)

    # Check accessibility
    results["accessibility"] = check_accessibility()

    return results


def full_uninstall(remove_data: bool = False) -> dict[str, bool]:
    """Run the full uninstallation workflow."""
    results: dict[str, bool] = {}

    # Unload and remove plist
    results["launchd"] = uninstall_launchd()

    # Optionally remove data
    if remove_data:
        try:
            db_path = LURK_DIR / "store.db"
            if db_path.exists():
                db_path.unlink()
            # Remove WAL/SHM files
            for suffix in ["-wal", "-shm"]:
                wal = LURK_DIR / f"store.db{suffix}"
                if wal.exists():
                    wal.unlink()
            results["data_removed"] = True
        except Exception:
            results["data_removed"] = False
    else:
        results["data_removed"] = False

    return results
