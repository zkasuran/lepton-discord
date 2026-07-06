"""The priced tool catalog the agent reasons over.

Each tool maps to an executor command (run server-side behind x402) and a price
in USDC atomic units. The agent decides which tool, if any, is worth buying for a
given request and whether it fits the remaining budget.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    command: str  # executor command_name run behind the x402 paywall
    description: str  # shown to the planner so it can choose
    price_atomic: int  # USDC atomic units (6 decimals)
    arg_name: str  # the single free-text argument the command takes
    arg_description: str

    @property
    def price_display(self) -> str:
        return f"${self.price_atomic / 1_000_000:.4f}"


# Sub-cent paid services. The agent pays per call from its own wallet.
TOOL_CATALOG: list[ToolSpec] = [
    ToolSpec(
        name="crypto_price",
        command="price",
        description="Live spot price and 24h change for a crypto asset (CoinGecko).",
        price_atomic=1000,  # $0.001
        arg_name="symbol",
        arg_description="Ticker symbol, e.g. BTC, ETH, SOL",
    ),
    ToolSpec(
        name="weather",
        command="weather",
        description="Current weather (temp, conditions, humidity, wind) for a city (Open-Meteo).",
        price_atomic=1000,  # $0.001
        arg_name="city",
        arg_description="City name, e.g. Tokyo, London",
    ),
    ToolSpec(
        name="news",
        command="news",
        description=(
            "Latest real news headlines on any topic, person, team or event (Google News). "
            "Use for current events the agent cannot know: recent results, breaking news, "
            "who won, what just happened."
        ),
        price_atomic=1000,  # $0.001
        arg_name="topic",
        arg_description="What to get news about, e.g. 'World Cup', 'Trump', 'Tesla earnings'",
    ),
    ToolSpec(
        name="deep_answer",
        command="ask",
        description=(
            "A premium, long-form researched answer from a larger model. Use only when the "
            "question needs depth the agent cannot give for free."
        ),
        price_atomic=10000,  # $0.01
        arg_name="prompt",
        arg_description="The full question to answer",
    ),
]

_BY_NAME = {t.name: t for t in TOOL_CATALOG}


def get_tool(name: str) -> ToolSpec | None:
    return _BY_NAME.get(name)
