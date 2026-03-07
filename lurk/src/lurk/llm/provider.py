"""LLM provider — Ollama by default, raw screen text as fallback.

Ollama runs locally, costs nothing, and keeps data on-machine.
lurk auto-detects it, auto-pulls the model, and uses it.
No API keys, no config, no cost.

If Ollama isn't available, the raw screen text goes directly to the
consuming agent (which IS an LLM). Still works, just less synthesized.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger("lurk.llm")

DEFAULT_MODEL = "llama3.2:3b"
OLLAMA_URL = "http://localhost:11434"


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    text: str
    model: str
    tokens_used: int = 0


class LLMProvider:
    """Ollama LLM provider — local, free, auto-detected."""

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = OLLAMA_URL) -> None:
        self.model = model
        self.base_url = base_url
        self._available: bool | None = None

    @property
    def name(self) -> str:
        return "ollama"

    def is_available(self) -> bool:
        """Check if Ollama is running and has the model."""
        if self._available is not None:
            return self._available
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
                models = [m.get("name", "") for m in data.get("models", [])]
                self._available = any(self.model in m for m in models)
                if not self._available:
                    logger.debug("Ollama running but model %s not found", self.model)
                return self._available
        except Exception:
            self._available = False
            return False

    def generate(self, prompt: str, system: str = "", max_tokens: int = 500) -> LLMResponse | None:
        """Generate a response. Returns None on failure."""
        try:
            payload = {
                "model": self.model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": 0.3,
                },
            }
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return LLMResponse(
                    text=result.get("response", "").strip(),
                    model=self.model,
                    tokens_used=result.get("eval_count", 0),
                )
        except Exception as e:
            logger.debug("Ollama generate failed: %s", e)
            return None


def detect_ollama() -> bool:
    """Check if Ollama is running."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False


def ensure_ollama_model(model: str = DEFAULT_MODEL) -> bool:
    """Pull the model if Ollama is running but doesn't have it."""
    try:
        # Check if model exists
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            if any(model in m for m in models):
                return True

        # Pull it
        logger.info("Pulling Ollama model %s...", model)
        payload = json.dumps({"name": model, "stream": False}).encode()
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            resp.read()
        return True
    except Exception as e:
        logger.debug("Failed to ensure model: %s", e)
        return False


def create_provider(config: dict | None = None) -> LLMProvider | None:
    """Create an Ollama provider if available. Returns None if not.

    config: optional dict with 'model' and/or 'ollama_url' overrides.
    """
    config = config or {}
    model = config.get("model", DEFAULT_MODEL)
    base_url = config.get("ollama_url", OLLAMA_URL)
    provider = LLMProvider(model=model, base_url=base_url)
    if provider.is_available():
        logger.info("Ollama detected with model %s", provider.model)
        return provider
    # Try pulling the model
    if detect_ollama():
        if ensure_ollama_model(model):
            provider._available = None  # reset cache
            if provider.is_available():
                return provider
    return None
