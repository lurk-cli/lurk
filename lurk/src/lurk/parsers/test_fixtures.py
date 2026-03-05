"""Test fixtures — real window titles with expected parse outputs for regression testing."""

from __future__ import annotations

# Each entry: (title, app, bundle_id, expected_fields)
# expected_fields is a dict of ParsedContext field → expected value.
# Only fields that should be non-default are listed.

FIXTURES: list[tuple[str, str, str | None, dict]] = [
    # --- VS Code ---
    (
        "main.py — my-project — Visual Studio Code",
        "Visual Studio Code",
        "com.microsoft.VSCode",
        {
            "parser_name": "vscode",
            "file": "main.py",
            "project": "my-project",
            "language": "python",
            "activity": "coding",
        },
    ),
    (
        "● settings.json — .vscode — Visual Studio Code",
        "Visual Studio Code",
        "com.microsoft.VSCode",
        {
            "parser_name": "vscode",
            "file": "settings.json",
            "project": ".vscode",
            "unsaved": True,
            "language": "json",
            "activity": "coding",
        },
    ),
    (
        "test_auth.py — backend — Cursor",
        "Cursor",
        "com.todesktop.230313mzl4w4u92",
        {
            "parser_name": "vscode",
            "file": "test_auth.py",
            "project": "backend",
            "language": "python",
            "activity": "coding",
            "sub_activity": "testing",
        },
    ),
    (
        "Welcome — Visual Studio Code",
        "Visual Studio Code",
        "com.microsoft.VSCode",
        {
            "parser_name": "vscode",
            "activity": "coding",
            "sub_activity": "configuring",
        },
    ),
    (
        "PROJ-1234 fix: api.ts — feature/PROJ-1234 (PROJ-1234-fix-auth) — Visual Studio Code",
        "Visual Studio Code",
        "com.microsoft.VSCode",
        {
            "parser_name": "vscode",
            "file": "PROJ-1234 fix: api.ts",
            "activity": "coding",
            "ticket": "PROJ-1234",
            "language": "typescript",
        },
    ),
    # --- Chrome ---
    (
        "How to use asyncio in Python - Stack Overflow — Google Chrome",
        "Google Chrome",
        "com.google.Chrome",
        {
            "parser_name": "chrome",
            "activity": "researching",
            "sub_activity": "stack_overflow",
        },
    ),
    (
        "Pull Request #42: Fix auth bug · my-org/backend — Google Chrome",
        "Google Chrome",
        "com.google.Chrome",
        {
            "parser_name": "chrome",
            "activity": "coding",
            "sub_activity": "code_review",
        },
    ),
    (
        "Reddit - Pair programming with AI — Google Chrome",
        "Google Chrome",
        "com.google.Chrome",
        {
            "parser_name": "chrome",
            "activity": "browsing",
            "sub_activity": "social",
        },
    ),
    # --- Safari ---
    (
        "MDN Web Docs - Array.prototype.map()",
        "Safari",
        "com.apple.Safari",
        {
            "parser_name": "safari",
            "activity": "researching",
            "sub_activity": "documentation",
        },
    ),
    # --- Slack ---
    (
        "#engineering - Acme Corp - Slack",
        "Slack",
        "com.tinyspeck.slackmacgap",
        {
            "parser_name": "slack",
            "activity": "communicating",
            "channel": "#engineering",
            "project": "Acme Corp",
        },
    ),
    (
        "Thread in #bugs - Acme Corp - Slack",
        "Slack",
        "com.tinyspeck.slackmacgap",
        {
            "parser_name": "slack",
            "activity": "communicating",
            "sub_activity": "thread",
            "channel": "#bugs",
            "project": "Acme Corp",
        },
    ),
    (
        "Jane Smith - Acme Corp - Slack",
        "Slack",
        "com.tinyspeck.slackmacgap",
        {
            "parser_name": "slack",
            "activity": "communicating",
            "sub_activity": "direct_message",
            "channel": "Jane Smith",
            "project": "Acme Corp",
        },
    ),
    # --- Terminal ---
    (
        "~/Projects/backend — zsh",
        "Terminal",
        "com.apple.Terminal",
        {
            "parser_name": "terminal",
            "activity": "coding",
            "project": "backend",
        },
    ),
    (
        "ssh user@prod-server.example.com",
        "iTerm2",
        "com.googlecode.iterm2",
        {
            "parser_name": "terminal",
            "activity": "coding",
            "sub_activity": "ssh",
        },
    ),
    (
        "python manage.py runserver — ~/Projects/webapp",
        "Terminal",
        "com.apple.Terminal",
        {
            "parser_name": "terminal",
            "activity": "coding",
            "sub_activity": "running",
            "project": "webapp",
        },
    ),
    # --- Xcode ---
    (
        "AppDelegate.swift — MaiDaemon — Xcode",
        "Xcode",
        "com.apple.dt.Xcode",
        {
            "parser_name": "xcode",
            "file": "AppDelegate.swift",
            "project": "MaiDaemon",
            "language": "swift",
            "activity": "coding",
        },
    ),
    (
        "MaiDaemon — Xcode",
        "Xcode",
        "com.apple.dt.Xcode",
        {
            "parser_name": "xcode",
            "project": "MaiDaemon",
            "activity": "coding",
        },
    ),
    # --- Xcode edge case: just "Xcode" ---
    (
        "Xcode",
        "Xcode",
        "com.apple.dt.Xcode",
        {
            "parser_name": "xcode",
            "activity": "coding",
        },
    ),
    # --- Empty title edge cases ---
    (
        "",
        "Visual Studio Code",
        "com.microsoft.VSCode",
        {
            "parser_name": "vscode",
            "activity": "coding",
        },
    ),
    (
        "",
        "Slack",
        "com.tinyspeck.slackmacgap",
        {
            "parser_name": "slack",
            "activity": "communicating",
        },
    ),
]


def run_fixtures(registry) -> tuple[int, int, list[str]]:
    """Run all fixtures against a parser registry.

    Returns (passed, total, error_messages).
    """
    passed = 0
    total = len(FIXTURES)
    errors: list[str] = []

    for title, app, bundle_id, expected in FIXTURES:
        ctx = registry.parse(title, app, bundle_id)
        for field_name, expected_val in expected.items():
            actual = getattr(ctx, field_name, None)
            if actual != expected_val:
                errors.append(
                    f"FAIL [{app}] {title!r}: {field_name}={actual!r}, expected {expected_val!r}"
                )
                break
        else:
            passed += 1

    return passed, total, errors
