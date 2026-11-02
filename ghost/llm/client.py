"""Asynchronous Ollama client for local model generation."""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite
from typing import Any

import httpx

from ghost.config import get_settings


class LLMError(RuntimeError):
    """Base error for Ollama generation failures."""


class LLMConnectionError(LLMError):
    """Ollama could not be reached."""


class LLMTimeoutError(LLMError):
    """Ollama did not complete generation within the configured timeout."""


class LLMResponseError(LLMError):
    """Ollama returned an invalid response."""


@dataclass(frozen=True)
class LLMResponse:
    """Normalized Ollama generation response."""

    text: str
    model: str
    done: bool
    structured: Any = None


class OllamaClient:
    """Small async client for Ollama's non-streaming generation endpoint."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (settings.ollama_host if base_url is None else base_url).rstrip("/")
        self.model = settings.ollama_default_model if model is None else model
        self.timeout = timeout if timeout is not None else settings.ollama_timeout_seconds
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Ollama base URL must use HTTP or HTTPS")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Ollama model name cannot be empty")
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, int | float)
            or not isfinite(self.timeout)
            or self.timeout <= 0
        ):
            raise ValueError("Ollama timeout must be positive")

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        format: str | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Generate one response without streaming partial tokens."""
        selected_model = self.model if model is None else model
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Generation prompt cannot be empty")
        if not isinstance(selected_model, str) or not selected_model.strip():
            raise ValueError("Ollama model name cannot be empty")
        if system is not None and not isinstance(system, str):
            raise ValueError("Ollama system prompt must be a string")
        if format is not None and not isinstance(format, str | dict):
            raise ValueError("Ollama format must be 'json' or a JSON schema object")
        if options is not None and not isinstance(options, dict):
            raise ValueError("Ollama options must be an object")

        payload: dict[str, Any] = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
        }
        if system is not None and system.strip():
            payload["system"] = system
        if format is not None:
            payload["format"] = format
        if options:
            payload["options"] = options

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("Ollama generation timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMResponseError(f"Ollama returned HTTP {exc.response.status_code}") from exc
        except httpx.TransportError as exc:
            raise LLMConnectionError("Could not connect to Ollama") from exc

        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise LLMResponseError("Ollama returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise LLMResponseError("Ollama response must be a JSON object")
        if data.get("error"):
            raise LLMResponseError(f"Ollama generation failed: {data['error']}")
        text = data.get("response")
        if not isinstance(text, str):
            raise LLMResponseError("Ollama response is missing generated text")
        done = data.get("done", True)
        if not isinstance(done, bool):
            raise LLMResponseError("Ollama response has an invalid completion status")

        structured: Any = None
        with suppress(json.JSONDecodeError):
            structured = json.loads(text)
        if (
            format is not None
            and (format == "json" or isinstance(format, dict))
            and structured is None
        ):
            raise LLMResponseError("Ollama did not return valid structured JSON")

        return LLMResponse(
            text=text,
            model=str(data.get("model") or selected_model),
            done=done,
            structured=structured,
        )
