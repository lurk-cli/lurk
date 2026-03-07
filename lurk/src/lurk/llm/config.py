"""LLM configuration — auto-detect Ollama, no config needed.

Ollama runs locally, costs nothing, and keeps data on-machine.
lurk auto-detects it, auto-pulls the model, and uses it.
No API keys, no config files, no cost.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("lurk.llm")

DEFAULT_CONFIG_PATH = Path.home() / ".lurk" / "config.yaml"


def load_llm_config() -> dict:
    """Load optional LLM overrides from config YAML.

    Returns a dict with optional keys: model, ollama_url.
    If no config exists, returns empty dict (Ollama auto-detected with defaults).
    """
    config_path = DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {}

    try:
        import yaml
    except ImportError:
        return {}

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        llm_section = data.get("llm", {})
        if not llm_section:
            return {}

        result = {}
        if "model" in llm_section:
            result["model"] = llm_section["model"]
        if "ollama_url" in llm_section:
            result["ollama_url"] = llm_section["ollama_url"]
        return result
    except Exception:
        logger.exception("Failed to load LLM config")
        return {}
