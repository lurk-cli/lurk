"""LLM configuration loader — reads from lurk config YAML."""

from __future__ import annotations

import logging
from pathlib import Path

from .provider import LLMConfig

logger = logging.getLogger("lurk.llm")

DEFAULT_CONFIG_PATH = Path.home() / ".lurk" / "config.yaml"


def load_llm_config(config_path: Path | None = None) -> LLMConfig:
    """Load LLM configuration from the lurk config file.

    Returns default (provider=none) if no config file exists or LLM section is missing.
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    if not config_path.exists():
        return LLMConfig()

    try:
        import yaml
    except ImportError:
        logger.debug("pyyaml not installed, using default LLM config")
        return LLMConfig()

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        llm_section = data.get("llm", {})
        if not llm_section:
            return LLMConfig()

        return LLMConfig(
            provider=llm_section.get("provider", "none"),
            model=llm_section.get("model", "llama3.2:3b"),
            api_key=llm_section.get("api_key"),
            ollama_url=llm_section.get("ollama_url", "http://localhost:11434"),
            use_for=llm_section.get("use_for", [
                "prompt_generation", "intent_classification", "session_summaries",
            ]),
            fallback=llm_section.get("fallback", "rules"),
            timeout=llm_section.get("timeout", 5.0),
        )
    except Exception:
        logger.exception("Failed to load LLM config, using defaults")
        return LLMConfig()
