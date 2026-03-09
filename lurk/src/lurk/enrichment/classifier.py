"""Activity classifier — rules-based app+context → activity type."""

from __future__ import annotations

from functools import lru_cache

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
}


@lru_cache(maxsize=256)
def classify_activity(app: str, title: str | None = None) -> str:
    """Classify activity from app name and optional title."""
    app_lower = app.lower()

    # Check app rules
    for keyword, activity in _APP_RULES.items():
        if keyword in app_lower:
            return activity

    return "unknown"


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
