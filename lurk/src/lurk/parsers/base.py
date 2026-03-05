"""Base parser types and protocol."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedContext:
    """Structured context extracted from a window title."""

    app: str
    file: str | None = None
    project: str | None = None
    language: str | None = None
    ticket: str | None = None
    branch: str | None = None
    url_domain: str | None = None
    topic: str | None = None
    channel: str | None = None
    document_name: str | None = None
    unsaved: bool = False
    activity: str = "unknown"
    sub_activity: str | None = None
    parser_name: str = "fallback"

    def validate(self) -> bool:
        """Return True if any meaningful field beyond `app` was extracted."""
        meaningful = (
            self.file, self.project, self.language, self.ticket,
            self.branch, self.url_domain, self.topic, self.channel,
            self.document_name,
        )
        if any(v is not None for v in meaningful):
            return True
        if self.activity != "unknown":
            return True
        if self.sub_activity is not None:
            return True
        if self.unsaved:
            return True
        return False

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None and v is not False}


# Extension → language mapping
EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".rs": "rust",
    ".go": "go", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".swift": "swift", ".rb": "ruby", ".php": "php",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".cs": "csharp", ".scala": "scala", ".zig": "zig",
    ".lua": "lua", ".r": "r", ".R": "r",
    ".sql": "sql", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".html": "html", ".css": "css", ".scss": "scss", ".less": "less",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".xml": "xml", ".md": "markdown", ".mdx": "markdown",
    ".vue": "vue", ".svelte": "svelte", ".dart": "dart",
    ".ex": "elixir", ".exs": "elixir", ".erl": "erlang",
    ".hs": "haskell", ".ml": "ocaml", ".fs": "fsharp",
}


def language_from_filename(filename: str) -> str | None:
    """Detect programming language from file extension."""
    for ext, lang in EXTENSION_LANGUAGES.items():
        if filename.endswith(ext):
            return lang
    return None


def detect_file_role(filename: str) -> str | None:
    """Detect the role of a file from its name."""
    lower = filename.lower()
    if any(p in lower for p in ["test", "spec", "_test.", ".test.", ".spec."]):
        return "testing"
    if lower.startswith("readme"):
        return "documentation"
    if lower in ("changelog.md", "contributing.md", "license", "license.md"):
        return "documentation"
    if lower.startswith("dockerfile") or lower in ("docker-compose.yml", "docker-compose.yaml"):
        return "devops"
    if lower in (".github", ".gitlab-ci.yml", "jenkinsfile"):
        return "ci_cd"
    if lower in ("package.json", "cargo.toml", "pyproject.toml", "go.mod"):
        return "config"
    return None


class AppParser:
    """Base class for app-specific title parsers."""

    name: str = "base"
    app_names: list[str] = []
    bundle_ids: list[str] = []

    def can_parse(self, app: str, bundle_id: str | None = None) -> bool:
        """Check if this parser handles the given app."""
        app_lower = app.lower()
        if any(name.lower() in app_lower for name in self.app_names):
            return True
        if bundle_id and any(bid in (bundle_id or "") for bid in self.bundle_ids):
            return True
        return False

    def parse(self, title: str, app: str, bundle_id: str | None = None) -> ParsedContext:
        """Parse a window title into structured context."""
        return ParsedContext(app=app, parser_name=self.name)
