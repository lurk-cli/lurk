"""Configuration loader — reads YAML config into structured settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("lurk.config")

DEFAULT_CONFIG_PATH = Path.home() / ".lurk" / "config.yaml"


@dataclass
class ObservationConfig:
    poll_interval: float = 3.0
    idle_threshold: float = 120.0
    session_gap: float = 300.0


@dataclass
class ExclusionConfig:
    apps: list[str] = field(default_factory=list)
    title_patterns: list[str] = field(default_factory=list)
    bundle_ids: list[str] = field(default_factory=list)
    time_blocks: list[dict] = field(default_factory=list)


@dataclass
class ContextFilesConfig:
    enabled: bool = True
    targets: list[str] = field(default_factory=lambda: ["lurk_context"])
    update_interval: float = 30.0


@dataclass
class RetentionConfig:
    raw_events_days: int = 30
    sessions_days: int = 365
    enriched_events_days: int = 90


@dataclass
class HttpConfig:
    host: str = "127.0.0.1"
    port: int = 4141


@dataclass
class PromptConfig:
    max_tokens: int = 250
    research_staleness_minutes: int = 30
    cross_session_max_hours: int = 24
    min_files_for_inclusion: int = 2
    monitor_reference_apps: list[str] = field(default_factory=lambda: [
        "Google Chrome", "Safari", "Arc", "Brave Browser", "Firefox",
        "Microsoft Edge", "Notion", "Confluence",
    ])


@dataclass
class AgentConfig:
    """Agent awareness configuration."""
    enabled: bool = True
    stale_timeout: float = 600.0


@dataclass
class LurkConfig:
    """Full lurk configuration."""
    observation: ObservationConfig = field(default_factory=ObservationConfig)
    exclusions: ExclusionConfig = field(default_factory=ExclusionConfig)
    context_files: ContextFilesConfig = field(default_factory=ContextFilesConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    prompt: PromptConfig = field(default_factory=PromptConfig)
    agents: AgentConfig = field(default_factory=AgentConfig)


def load_config(config_path: Path | None = None) -> LurkConfig:
    """Load configuration from YAML file. Returns defaults if not found."""
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    if not config_path.exists():
        return LurkConfig()

    try:
        import yaml
    except ImportError:
        logger.debug("pyyaml not installed, using default config")
        return LurkConfig()

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        config = LurkConfig()

        # Observation
        obs = data.get("observation", {})
        if obs:
            config.observation = ObservationConfig(
                poll_interval=obs.get("poll_interval", 3.0),
                idle_threshold=obs.get("idle_threshold", 120.0),
                session_gap=obs.get("session_gap", 300.0),
            )

        # Exclusions
        exc = data.get("exclusions", {})
        if exc:
            config.exclusions = ExclusionConfig(
                apps=exc.get("apps", []),
                title_patterns=exc.get("title_patterns", []),
                bundle_ids=exc.get("bundle_ids", []),
                time_blocks=exc.get("time_blocks", []),
            )

        # Context files
        cf = data.get("context_files", {})
        if cf:
            config.context_files = ContextFilesConfig(
                enabled=cf.get("enabled", True),
                targets=cf.get("targets", ["mai_context"]),
                update_interval=cf.get("update_interval", 30.0),
            )

        # Retention
        ret = data.get("retention", {})
        if ret:
            config.retention = RetentionConfig(
                raw_events_days=ret.get("raw_events_days", 30),
                sessions_days=ret.get("sessions_days", 365),
                enriched_events_days=ret.get("enriched_events_days", 90),
            )

        # HTTP
        http = data.get("http", {})
        if http:
            config.http = HttpConfig(
                host=http.get("host", "127.0.0.1"),
                port=http.get("port", 4141),
            )

        # Prompt
        pr = data.get("prompt", {})
        if pr:
            config.prompt = PromptConfig(
                max_tokens=pr.get("max_tokens", 250),
                research_staleness_minutes=pr.get("research_staleness_minutes", 30),
                cross_session_max_hours=pr.get("cross_session_max_hours", 24),
                min_files_for_inclusion=pr.get("min_files_for_inclusion", 2),
                monitor_reference_apps=pr.get("monitor_reference_apps", PromptConfig().monitor_reference_apps),
            )

        # Agents
        ag = data.get("agents", {})
        if ag:
            config.agents = AgentConfig(
                enabled=ag.get("enabled", True),
                stale_timeout=ag.get("stale_timeout", 600.0),
            )

        return config

    except Exception:
        logger.exception("Failed to load config, using defaults")
        return LurkConfig()
