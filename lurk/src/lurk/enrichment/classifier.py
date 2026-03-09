"""Activity classifier — rules-based app+context → activity type."""

from __future__ import annotations

import re
from functools import lru_cache

# File extensions that suggest coding activity
_CODE_EXT_PATTERN = re.compile(
    r"\.(py|ts|tsx|js|jsx|go|rs|rb|java|kt|scala|cpp|c|h|cs|swift|lua|zig|"
    r"toml|yaml|yml|json|xml|html|css|scss|sql|sh|bash|zsh|dockerfile|tf|proto)"
    r"(\s|$|:|\b)",
    re.IGNORECASE,
)

# App name keywords → activity
_APP_RULES: dict[str, str] = {
    "code": "coding",
    "cursor": "coding",
    "xcode": "coding",
    "intellij": "coding",
    "webstorm": "coding",
    "pycharm": "coding",
    "goland": "coding",
    "rubymine": "coding",
    "phpstorm": "coding",
    "clion": "coding",
    "rider": "coding",
    "android studio": "coding",
    "vim": "coding",
    "neovim": "coding",
    "sublime": "coding",
    "terminal": "coding",
    "iterm": "coding",
    "warp": "coding",
    "alacritty": "coding",
    "kitty": "coding",
    "slack": "communicating",
    "teams": "communicating",
    "discord": "communicating",
    "wechat": "communicating",
    "微信": "communicating",
    "whatsapp": "communicating",
    "telegram": "communicating",
    "signal": "communicating",
    "messages": "communicating",
    "mail": "communicating",
    "outlook": "communicating",
    "zoom": "meeting",
    "facetime": "meeting",
    "meet": "meeting",
    "figma": "designing",
    "sketch": "designing",
    "photoshop": "designing",
    "illustrator": "designing",
    "notion": "writing",
    "obsidian": "writing",
    "pages": "writing",
    "word": "writing",
    "textedit": "writing",
    "excel": "spreadsheet_work",
    "numbers": "spreadsheet_work",
    "powerpoint": "writing",
    "keynote": "writing",
    "preview": "reading",
    "finder": "browsing",
    "chrome": "browsing",
    "safari": "browsing",
    "firefox": "browsing",
    "arc": "browsing",
    "brave": "browsing",
    "edge": "browsing",
    "linear": "planning",
    "jira": "planning",
    "final cut": "video_editing",
    "davinci resolve": "video_editing",
    "premiere": "video_editing",
    "after effects": "video_editing",
    "indesign": "designing",
    "canva": "designing",
    "tableau": "data_analysis",
    "power bi": "data_analysis",
    "asana": "planning",
    "monday": "planning",
    "trello": "planning",
    "productboard": "planning",
    # Media playback
    "quicktime": "media_playback",
    "quicktime player": "media_playback",
    "mpv": "media_playback",
    "iina": "media_playback",
    "vlc": "media_playback",
    "podcasts": "media_playback",
    "music": "media_playback",
    "spotify": "media_playback",
    # Audio editing
    "audacity": "audio_editing",
    "garageband": "audio_editing",
    "logic pro": "audio_editing",
    # Writing
    "notes": "writing",
    "bear": "writing",
    "ia writer": "writing",
    "ulysses": "writing",
    "typora": "writing",
    "google docs": "writing",
    "google slides": "writing",
    "coda": "writing",
    # Spreadsheet work
    "google sheets": "spreadsheet_work",
    "airtable": "spreadsheet_work",
    # Designing
    "miro": "designing",
    "whimsical": "designing",
    "lucidchart": "designing",
    # System
    "system preferences": "system",
    "system settings": "system",
    "activity monitor": "system",
    "console": "system",
    "1password": "system",
    "keychain": "system",
    "bitwarden": "system",
    # Coding tools
    "docker": "coding",
    "postman": "coding",
    "insomnia": "coding",
    "datagrip": "coding",
    "dbeaver": "coding",
    "sequel pro": "coding",
    "tower": "coding",
    "gitkraken": "coding",
    "sourcetree": "coding",
    "github desktop": "coding",
    # Meeting
    "google meet": "meeting",
    "webex": "meeting",
    "around": "meeting",
    # Recording
    "loom": "recording",
    "obs": "recording",
    "screenflow": "recording",
    # Planning
    "notion calendar": "planning",
    "fantastical": "planning",
    "todoist": "planning",
    "things": "planning",
    "omnifocus": "planning",
    "clickup": "planning",
    "height": "planning",
    "shortcut": "planning",
}


@lru_cache(maxsize=256)
def classify_activity(app: str, title: str | None = None) -> str:
    """Classify activity from app name and optional title."""
    app_lower = app.lower()

    # Check app rules
    for keyword, activity in _APP_RULES.items():
        if keyword in app_lower:
            return activity

    # Smart fallback: infer from title when no app rule matched
    if title:
        title_lower = title.lower()
        if _CODE_EXT_PATTERN.search(title_lower):
            return "coding"
        if "http://" in title_lower or "https://" in title_lower or "www." in title_lower:
            return "browsing"

    return "general"


def classify_interruptibility(
    activity: str, duration_seconds: float, input_state: str = "idle"
) -> str:
    """Estimate interruptibility based on activity and focus depth."""
    if activity == "meeting":
        return "low"

    if activity == "coding" and duration_seconds > 900:  # >15 min
        return "low"

    if activity == "coding" and input_state == "typing":
        return "low"

    if activity == "video_editing" and duration_seconds > 600:
        return "low"

    if activity in ("data_analysis", "designing") and duration_seconds > 900:
        return "low"

    if activity in ("marketing", "sales", "support", "planning"):
        return "medium"

    if activity in ("communicating", "browsing"):
        return "high"

    if duration_seconds < 120:  # <2 min
        return "high"

    return "medium"
