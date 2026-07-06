"""Tests for command executors.

Network IO is mocked: dispatch tests monkeypatch the IO helpers, and the
parsing tests drive the real helpers against canned HTTP responses via
pytest-httpx. No live network is touched.
"""

from __future__ import annotations

import pytest

from src.api import executor
from src.api.executor import execute_command


async def test_unknown_command() -> None:
    result = await execute_command("nonexistent", {})
    assert "Unknown" in result


async def test_ping_command() -> None:
    result = await execute_command("ping", {})
    assert "Pong" in result and "Arc" in result


async def test_price_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(symbol: str) -> str:
        return f"{symbol.upper()} = $67,420 (CoinGecko)"

    monkeypatch.setattr(executor, "_fetch_price", fake)
    result = await execute_command("price", {"symbol": "btc"})
    assert "BTC" in result and "67,420" in result


async def test_weather_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(city: str) -> str:
        return f"{city}, JP: 21°C, clear sky. Humidity 50%, wind 8 km/h."

    monkeypatch.setattr(executor, "_fetch_weather", fake)
    result = await execute_command("weather", {"city": "Tokyo"})
    assert "Tokyo" in result and "°C" in result


async def test_news_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(topic: str) -> str:
        return f"Latest on '{topic}':\n- Headline one\n- Headline two"

    monkeypatch.setattr(executor, "_fetch_news", fake)
    result = await execute_command("news", {"topic": "World Cup"})
    assert "World Cup" in result and "Headline one" in result


async def test_news_service_error_is_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(topic: str) -> str:
        raise RuntimeError("rss down")

    monkeypatch.setattr(executor, "_fetch_news", boom)
    result = await execute_command("news", {"topic": "Trump"})
    assert "unavailable" in result.lower()


async def test_fetch_news_parses_rss(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    rss = (
        "<rss><channel><title>Google News</title>"
        "<item><title>Mexico 2-3 England: World Cup last 16</title></item>"
        "<item><title>Trump asks FIFA to review suspension</title></item>"
        "<item><title>Third story &amp; more</title></item>"
        "<item><title>Fourth story (should be dropped)</title></item>"
        "</channel></rss>"
    )
    httpx_mock.add_response(text=rss)
    result = await executor._fetch_news("world cup")
    assert "world cup" in result
    assert "Mexico 2-3 England" in result
    assert "Third story & more" in result  # entity decoded
    assert "Fourth story" not in result  # capped at 3


async def test_ask_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(prompt: str) -> str:
        return f"Answer to: {prompt}"

    monkeypatch.setattr(executor, "_ask_claude", fake)
    result = await execute_command("ask", {"prompt": "hello"})
    assert "hello" in result


async def test_gpt_is_alias_of_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(prompt: str) -> str:
        return "claude says hi"

    monkeypatch.setattr(executor, "_ask_claude", fake)
    result = await execute_command("gpt", {"prompt": "anything"})
    assert result == "claude says hi"


async def test_ask_empty_prompt() -> None:
    result = await execute_command("ask", {"prompt": "  "})
    assert "Ask me something" in result


async def test_price_service_error_is_graceful(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(symbol: str) -> str:
        raise RuntimeError("coingecko down")

    monkeypatch.setattr(executor, "_fetch_price", boom)
    result = await execute_command("price", {"symbol": "eth"})
    assert "ETH" in result and "unavailable" in result


async def test_fetch_price_parses_real_shape(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    # CoinGecko response shape -> our formatted line.
    httpx_mock.add_response(json={"ethereum": {"usd": 3521.42, "usd_24h_change": -1.23}})
    result = await executor._fetch_price("ETH")
    assert "ETH" in result and "3,521.42" in result and "-1.23%" in result


async def test_fetch_weather_parses_real_shape(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    # Two sequential calls: geocoding, then forecast. Responses returned in order.
    geo = {
        "results": [{"latitude": 35.6, "longitude": 139.7, "name": "Tokyo", "country_code": "JP"}]
    }
    forecast = {
        "current": {
            "temperature_2m": 22.0,
            "relative_humidity_2m": 60,
            "wind_speed_10m": 9.4,
            "weather_code": 2,
        }
    }
    httpx_mock.add_response(json=geo)
    httpx_mock.add_response(json=forecast)
    result = await executor._fetch_weather("Tokyo")
    assert "Tokyo, JP" in result and "22.0°C" in result and "partly cloudy" in result
