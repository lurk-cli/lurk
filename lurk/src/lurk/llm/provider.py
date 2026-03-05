"""LLM provider abstraction — Ollama (local) and cloud API key providers.

Supports:
- Ollama (local, privacy-preserving, auto-detected)
- Anthropic (cloud, user API key)
- OpenAI (cloud, user API key)
- None (rules-based fallback, the default)

Provider is never required. If unavailable, silently falls back to rules-based.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger("lurk.llm")


@dataclass
class LLMConfig:
    """LLM configuration from YAML/settings."""
    provider: str = "none"  # none | ollama | anthropic | openai
    model: str = "llama3.2:3b"
    api_key: str | None = None
    ollama_url: str = "http://localhost:11434"
    use_for: list[str] = field(default_factory=lambda: [
        "prompt_generation", "intent_classification", "session_summaries"
    ])
    fallback: str = "rules"
    timeout: float = 5.0  # seconds


@dataclass
class LLMResponse:
    """Response from an LLM provider."""
    text: str
    model: str
    provider: str
    tokens_used: int = 0


class LLMProvider(ABC):
    """Abstract LLM provider."""

    @abstractmethod
    def generate(self, prompt: str, system: str = "", max_tokens: int = 500) -> LLMResponse | None:
        """Generate a response. Returns None on failure (caller uses fallback)."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is ready to use."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name."""


class OllamaProvider(LLMProvider):
    """Local Ollama provider — privacy-preserving, auto-detected."""

    def __init__(self, config: LLMConfig) -> None:
        self.base_url = config.ollama_url
        self.model = config.model
        self.timeout = config.timeout
        self._available: bool | None = None

    @property
    def name(self) -> str:
        return "ollama"

    def is_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
                models = [m.get("name", "") for m in data.get("models", [])]
                self._available = any(self.model in m for m in models)
                if self._available:
                    logger.info("Ollama detected with model %s", self.model)
                else:
                    logger.warning("Ollama running but model %s not found. Available: %s",
                                   self.model, ", ".join(models[:5]))
                return self._available
        except Exception:
            self._available = False
            return False

    def generate(self, prompt: str, system: str = "", max_tokens: int = 500) -> LLMResponse | None:
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read())
                return LLMResponse(
                    text=result.get("response", "").strip(),
                    model=self.model,
                    provider="ollama",
                    tokens_used=result.get("eval_count", 0),
                )
        except Exception as e:
            logger.debug("Ollama generate failed: %s", e)
            return None


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider — requires user API key."""

    def __init__(self, config: LLMConfig) -> None:
        self.api_key = config.api_key
        self.model = config.model or "claude-haiku-4-5-20251001"
        self.timeout = config.timeout

    @property
    def name(self) -> str:
        return "anthropic"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, system: str = "", max_tokens: int = 500) -> LLMResponse | None:
        if not self.api_key:
            return None
        try:
            messages = [{"role": "user", "content": prompt}]
            payload: dict = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                payload["system"] = system

            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read())
                text = ""
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        text += block.get("text", "")
                usage = result.get("usage", {})
                return LLMResponse(
                    text=text.strip(),
                    model=self.model,
                    provider="anthropic",
                    tokens_used=usage.get("output_tokens", 0),
                )
        except Exception as e:
            logger.debug("Anthropic generate failed: %s", e)
            return None


class OpenAIProvider(LLMProvider):
    """OpenAI provider — requires user API key."""

    def __init__(self, config: LLMConfig) -> None:
        self.api_key = config.api_key
        self.model = config.model or "gpt-4o-mini"
        self.timeout = config.timeout

    @property
    def name(self) -> str:
        return "openai"

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, system: str = "", max_tokens: int = 500) -> LLMResponse | None:
        if not self.api_key:
            return None
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            }
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read())
                text = result["choices"][0]["message"]["content"]
                usage = result.get("usage", {})
                return LLMResponse(
                    text=text.strip(),
                    model=self.model,
                    provider="openai",
                    tokens_used=usage.get("completion_tokens", 0),
                )
        except Exception as e:
            logger.debug("OpenAI generate failed: %s", e)
            return None


def create_provider(config: LLMConfig) -> LLMProvider | None:
    """Create the appropriate LLM provider from config.

    Returns None if provider is 'none' or not configured.
    """
    if config.provider == "none":
        return None
    elif config.provider == "ollama":
        provider = OllamaProvider(config)
        if provider.is_available():
            return provider
        logger.info("Ollama not available, LLM features disabled")
        return None
    elif config.provider == "anthropic":
        provider = AnthropicProvider(config)
        if provider.is_available():
            return provider
        logger.warning("Anthropic API key not configured")
        return None
    elif config.provider == "openai":
        provider = OpenAIProvider(config)
        if provider.is_available():
            return provider
        logger.warning("OpenAI API key not configured")
        return None
    else:
        logger.warning("Unknown LLM provider: %s", config.provider)
        return None


def detect_ollama() -> bool:
    """Auto-detect if Ollama is running locally."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2):
            return True
    except Exception:
        return False
