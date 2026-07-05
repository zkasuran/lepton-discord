"""Tests for the LLM provider layer, focused on the OpenAI-compatible path.

The HTTP call is mocked, so provider selection and response parsing are exercised
offline and deterministically.
"""

from __future__ import annotations

import pytest

from src.agent import llm
from src.agent.tools import TOOL_CATALOG
from src.payments import config


@pytest.fixture
def openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://llm.example/v1")
    monkeypatch.setattr(config, "OPENAI_MODEL", "test-model")


def test_provider_autodetects_openai(openai_env: None) -> None:
    assert llm.provider() == "openai"
    assert llm.available() is True


def test_provider_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "")
    assert llm.provider() == "none"
    assert llm.available() is False


def test_explicit_provider_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "x")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://y/v1")
    assert llm.provider() == "anthropic"


async def test_openai_chat_returns_trimmed_content(
    openai_env: None,
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json={"choices": [{"message": {"content": "  hello world  "}}]})
    out = await llm.chat("hi", max_tokens=10)
    assert out == "hello world"


async def test_openai_plan_picks_a_tool(
    openai_env: None,
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(
        json={
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "crypto_price",
                                    "arguments": '{"symbol": "BTC"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )
    res = await llm.plan_tools("sys", "price of btc?", TOOL_CATALOG, "respond_directly")
    assert res["name"] == "crypto_price"
    assert res["args"] == {"symbol": "BTC"}


async def test_openai_plan_no_tool_call_is_free_answer(
    openai_env: None,
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(json={"choices": [{"message": {"content": "Here is the answer."}}]})
    res = await llm.plan_tools("sys", "hello", TOOL_CATALOG, "respond_directly")
    assert res["name"] == ""
    assert "answer" in res["text"].lower()


async def test_openai_plan_bad_arguments_json_degrades(
    openai_env: None,
    httpx_mock,  # type: ignore[no-untyped-def]
) -> None:
    httpx_mock.add_response(
        json={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "crypto_price", "arguments": "NOT JSON"}}
                        ]
                    }
                }
            ]
        }
    )
    res = await llm.plan_tools("sys", "x", TOOL_CATALOG, "respond_directly")
    assert res["name"] == "crypto_price"
    assert res["args"] == {}
