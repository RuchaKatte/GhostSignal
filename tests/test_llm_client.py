"""Tests for the asynchronous Ollama client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ghost.llm.client import (
    LLMConnectionError,
    LLMResponseError,
    LLMTimeoutError,
    OllamaClient,
)


class FakeAsyncClient:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.payload: dict[str, Any] | None = None

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, _url: str, *, json: dict[str, Any]) -> Any:
        self.payload = json
        if self.error:
            raise self.error
        return self.response


def _response(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "http://ollama/api/generate"),
    )


@pytest.mark.asyncio
async def test_generation_returns_parsed_json_and_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAsyncClient(_response({"model": "configured:latest", "response": '{"ok": true}'}))
    monkeypatch.setattr("ghost.llm.client.httpx.AsyncClient", lambda **_kwargs: fake)

    result = await OllamaClient(base_url="http://ollama", model="configured:latest").generate(
        "prompt", system="system", format="json"
    )

    assert result.structured == {"ok": True}
    assert result.model == "configured:latest"
    assert fake.payload == {
        "model": "configured:latest",
        "prompt": "prompt",
        "system": "system",
        "format": "json",
        "stream": False,
    }


@pytest.mark.asyncio
async def test_connection_timeout_and_malformed_responses_are_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAsyncClient(error=httpx.ConnectError("offline"))
    monkeypatch.setattr("ghost.llm.client.httpx.AsyncClient", lambda **_kwargs: fake)
    with pytest.raises(LLMConnectionError):
        await OllamaClient(base_url="http://ollama", model="test").generate("prompt")

    fake.error = httpx.ReadTimeout("late")
    with pytest.raises(LLMTimeoutError):
        await OllamaClient(base_url="http://ollama", model="test").generate("prompt")

    fake.error = None
    fake.response = _response({"model": "test"})
    with pytest.raises(LLMResponseError, match="generated text"):
        await OllamaClient(base_url="http://ollama", model="test").generate("prompt")


@pytest.mark.asyncio
async def test_http_errors_and_empty_prompts_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAsyncClient(_response({"error": "missing model"}, status_code=404))
    monkeypatch.setattr("ghost.llm.client.httpx.AsyncClient", lambda **_kwargs: fake)
    client = OllamaClient(base_url="http://ollama", model="test")
    with pytest.raises(LLMResponseError, match="HTTP 404"):
        await client.generate("prompt")
    with pytest.raises(ValueError, match="prompt"):
        await client.generate(" ")


@pytest.mark.asyncio
async def test_structured_responses_and_completion_status_are_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAsyncClient(_response({"model": "test", "response": "not-json", "done": True}))
    monkeypatch.setattr("ghost.llm.client.httpx.AsyncClient", lambda **_kwargs: fake)
    with pytest.raises(LLMResponseError, match="structured JSON"):
        await OllamaClient(base_url="http://ollama", model="test").generate(
            "prompt",
            format="json",
        )

    fake.response = _response({"model": "test", "response": '{"ok": true}', "done": "false"})
    with pytest.raises(LLMResponseError, match="completion status"):
        await OllamaClient(base_url="http://ollama", model="test").generate("prompt")


def test_ollama_rejects_invalid_configuration_and_request_options() -> None:
    with pytest.raises(ValueError, match="timeout"):
        OllamaClient(base_url="http://ollama", model="test", timeout=True)

    with pytest.raises(ValueError, match="model"):
        OllamaClient(base_url="http://ollama", model="")
