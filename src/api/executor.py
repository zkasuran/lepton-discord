"""Command executors — the real work done after payment is verified.

Each executor is an async function that receives the command arguments dict and
returns a string result. These run only after the x402 payment has settled on
Arc, so they are the "service" the agent paid for.

The network calls are isolated in small helpers (`_fetch_price`, `_fetch_weather`,
`_ask_claude`) so tests can monkeypatch them and stay offline.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger("nanopay.executor")

Executor = Callable[[dict[str, str]], Awaitable[str]]

# Registry: command_name -> async callable
_EXECUTORS: dict[str, Executor] = {}

_HTTP_TIMEOUT = 15.0

# Common ticker -> CoinGecko id (falls back to the lowercased symbol)
_COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "USDC": "usd-coin",
    "USDT": "tether",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ARB": "arbitrum",
    "MATIC": "matic-network",
}

# WMO weather codes -> short description
_WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "dense drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def register(name: str) -> Callable[[Executor], Executor]:
    """Decorator to register a command executor."""

    def decorator(fn: Executor) -> Executor:
        _EXECUTORS[name] = fn
        return fn

    return decorator


async def execute_command(command_name: str, args: dict[str, str]) -> str:
    """Dispatch to the right executor and return the result string."""
    fn = _EXECUTORS.get(command_name)
    if fn is None:
        return f"Unknown command: {command_name}"
    return await fn(args)


# ============================================================================
# IO helpers (monkeypatched in tests)
# ============================================================================


async def _fetch_price(symbol: str) -> str:
    """Live spot price from CoinGecko (no API key needed)."""
    coin_id = _COINGECKO_IDS.get(symbol.upper(), symbol.lower())
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        resp = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd", "include_24hr_change": "true"},
        )
        resp.raise_for_status()
        data = resp.json()
    quote = data.get(coin_id)
    if not quote:
        return f"{symbol.upper()}: no price found."
    usd = quote.get("usd")
    change = quote.get("usd_24h_change")
    change_str = f" ({change:+.2f}% 24h)" if isinstance(change, (int, float)) else ""
    return f"{symbol.upper()} = ${usd:,}{change_str} (CoinGecko)"


async def _fetch_weather(city: str) -> str:
    """Live current weather from Open-Meteo (no API key needed)."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        geo = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
        )
        geo.raise_for_status()
        results = geo.json().get("results") or []
        if not results:
            return f"Weather: could not find '{city}'."
        loc = results[0]
        lat, lon = loc["latitude"], loc["longitude"]
        place = loc.get("name", city)
        country = loc.get("country_code", "")

        wx = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
            },
        )
        wx.raise_for_status()
        cur = wx.json().get("current", {})
    temp = cur.get("temperature_2m")
    hum = cur.get("relative_humidity_2m")
    wind = cur.get("wind_speed_10m")
    desc = _WEATHER_CODES.get(int(cur.get("weather_code", -1)), "unknown")
    loc_label = f"{place}, {country}".rstrip(", ")
    return f"{loc_label}: {temp}°C, {desc}. Humidity {hum}%, wind {wind} km/h."


async def _ask_claude(prompt: str) -> str:
    """Real answer from the operator's model (Anthropic or an OpenAI-compatible API)."""
    from ..agent import llm

    if not llm.available():
        return "AI unavailable: no LLM configured."
    answer = await llm.chat(prompt, max_tokens=600)
    return answer or "(no answer)"


# ============================================================================
# Premium commands — the paid service
# ============================================================================


async def _fetch_news(topic: str) -> str:
    """Latest headlines for a topic from Google News RSS (no API key needed)."""
    from urllib.parse import quote

    url = f"https://news.google.com/rss/search?q={quote(topic)}&hl=en-US&gl=US&ceid=US:en"
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 NanoPay/1.0"})
        resp.raise_for_status()
        xml = resp.text

    # Parse the RSS: pull <item><title> entries, skip the feed's own title.
    items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    headlines: list[str] = []
    for item in items[:3]:
        m = re.search(r"<title>(.*?)</title>", item, re.DOTALL)
        if not m:
            continue
        title = m.group(1).strip()
        if title.startswith("<![CDATA[") and title.endswith("]]>"):
            title = title[9:-3].strip()
        title = title.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
        if title:
            headlines.append(title)
    if not headlines:
        return f"News for '{topic}': no headlines found right now."
    lines = "\n".join(f"- {h}" for h in headlines)
    return f"Latest on '{topic}':\n{lines}"


@register("price")
async def _price(args: dict[str, str]) -> str:
    symbol = args.get("symbol", "BTC")
    try:
        return await _fetch_price(symbol)
    except Exception as exc:  # noqa: BLE001 — surface a clean message, never 500
        logger.warning("price fetch failed: %s", exc)
        return f"{symbol.upper()}: price service unavailable right now."


@register("weather")
async def _weather(args: dict[str, str]) -> str:
    city = args.get("city", "London")
    try:
        return await _fetch_weather(city)
    except Exception as exc:  # noqa: BLE001
        logger.warning("weather fetch failed: %s", exc)
        return f"Weather for {city}: service unavailable right now."


@register("ask")
@register("gpt")
async def _ask(args: dict[str, str]) -> str:
    prompt = args.get("prompt", "").strip()
    if not prompt:
        return "Ask me something: /ask prompt:<your question>"
    try:
        return await _ask_claude(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("claude call failed: %s", exc)
        return "AI service unavailable right now."


@register("news")
async def _news(args: dict[str, str]) -> str:
    topic = args.get("topic", "").strip() or "world news"
    try:
        return await _fetch_news(topic)
    except Exception as exc:  # noqa: BLE001
        logger.warning("news fetch failed: %s", exc)
        return f"News for {topic}: service unavailable right now."


@register("ping")
async def _ping(args: dict[str, str]) -> str:  # noqa: ARG001
    """Smoke test — proves the x402 payment settled on Arc."""
    return "Pong. Payment settled on Arc via x402."
