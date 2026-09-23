"""
Unit tests for backend.app.genai.client (Groq-backed, see PROGRESS.md
"Batch 3G" for why this replaced the originally-planned Gemini client).
Named test_llm_client.py rather than test_gemini_client.py to match.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.genai import client as llm_client
from backend.app.genai.client import LLMUnavailable, generate_explanation


@pytest.fixture(autouse=True)
def _reset_client_singleton():
    llm_client._client = None
    yield
    llm_client._client = None


@pytest.mark.asyncio
async def test_no_api_key_raises_without_network_call(monkeypatch):
    monkeypatch.setattr("backend.app.genai.client.settings.groq_api_key", "")

    def _fail_if_constructed(*a, **kw):
        raise AssertionError("AsyncGroq must not be constructed when there is no API key")

    monkeypatch.setattr("backend.app.genai.client.AsyncGroq", _fail_if_constructed)

    with pytest.raises(LLMUnavailable) as exc_info:
        await generate_explanation("system", "user")
    assert exc_info.value.reason == "NO_API_KEY"


@pytest.mark.asyncio
async def test_timeout_raises_llm_unavailable(monkeypatch):
    import asyncio

    monkeypatch.setattr("backend.app.genai.client.settings.groq_api_key", "fake-key")
    monkeypatch.setattr("backend.app.genai.client.settings.groq_timeout_s", 0.05)

    async def _slow_create(*a, **kw):
        await asyncio.sleep(10)

    fake_client = MagicMock()
    fake_client.chat.completions.create = _slow_create
    monkeypatch.setattr("backend.app.genai.client.AsyncGroq", lambda **kw: fake_client)

    with pytest.raises(LLMUnavailable) as exc_info:
        await generate_explanation("system", "user")
    assert exc_info.value.reason == "TIMEOUT"


@pytest.mark.asyncio
async def test_non_json_content_raises_malformed(monkeypatch):
    monkeypatch.setattr("backend.app.genai.client.settings.groq_api_key", "fake-key")

    fake_message = MagicMock(content="not valid json {{{")
    fake_choice = MagicMock(message=fake_message)
    fake_response = MagicMock(choices=[fake_choice], model="openai/gpt-oss-20b", usage=None)
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_response)
    monkeypatch.setattr("backend.app.genai.client.AsyncGroq", lambda **kw: fake_client)

    with pytest.raises(LLMUnavailable) as exc_info:
        await generate_explanation("system", "user")
    assert exc_info.value.reason == "MALFORMED"


@pytest.mark.asyncio
async def test_empty_content_raises_malformed(monkeypatch):
    monkeypatch.setattr("backend.app.genai.client.settings.groq_api_key", "fake-key")

    fake_message = MagicMock(content="")
    fake_choice = MagicMock(message=fake_message)
    fake_response = MagicMock(choices=[fake_choice])
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_response)
    monkeypatch.setattr("backend.app.genai.client.AsyncGroq", lambda **kw: fake_client)

    with pytest.raises(LLMUnavailable) as exc_info:
        await generate_explanation("system", "user")
    assert exc_info.value.reason == "MALFORMED"


@pytest.mark.asyncio
async def test_api_error_raises_llm_unavailable(monkeypatch):
    monkeypatch.setattr("backend.app.genai.client.settings.groq_api_key", "fake-key")

    async def _raise(*a, **kw):
        raise ConnectionError("network down")

    fake_client = MagicMock()
    fake_client.chat.completions.create = _raise
    monkeypatch.setattr("backend.app.genai.client.AsyncGroq", lambda **kw: fake_client)

    with pytest.raises(LLMUnavailable) as exc_info:
        await generate_explanation("system", "user")
    assert exc_info.value.reason == "API_ERROR"


@pytest.mark.asyncio
async def test_valid_response_parses_and_returns_meta(monkeypatch):
    monkeypatch.setattr("backend.app.genai.client.settings.groq_api_key", "fake-key")

    valid_json = (
        '{"summary": "test", "probable_causes": [{"cause": "x", "evidence_refs": ["e1"], "likelihood": "HIGH"}], '
        '"recommended_actions": [{"action": "a", "rationale": "r", "knowledge_refs": []}], '
        '"lesson": {"title": "t", "tip": "tip", "knowledge_refs": ["k1"]}, "training_refs": [], "confidence": "HIGH"}'
    )
    fake_message = MagicMock(content=valid_json)
    fake_choice = MagicMock(message=fake_message)
    fake_usage = MagicMock()
    fake_usage.model_dump.return_value = {"total_tokens": 42}
    fake_response = MagicMock(choices=[fake_choice], model="openai/gpt-oss-20b", usage=fake_usage)
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_response)
    monkeypatch.setattr("backend.app.genai.client.AsyncGroq", lambda **kw: fake_client)

    parsed, meta = await generate_explanation("system", "user")
    assert parsed["summary"] == "test"
    assert meta["model"] == "openai/gpt-oss-20b"
    assert meta["usage"] == {"total_tokens": 42}
    assert meta["latency_ms"] >= 0


def test_llm_unavailable_str_includes_reason():
    err = LLMUnavailable("TIMEOUT", "exceeded 8.0s")
    assert "TIMEOUT" in str(err)
    assert "8.0s" in str(err)
