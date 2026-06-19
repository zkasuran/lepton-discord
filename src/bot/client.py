"""Discord bot: the autonomous buyer.

The agent pays from its own wallet, signing EIP-3009 (USDC only, no gas) while the
facilitator settles on-chain. No MetaMask and no human in the signing loop. A
manual MetaMask page exists as a fallback for the direct commands if the agent
wallet is empty.

Commands:
  /ask prompt:<text>       — the agent decides what (if anything) to pay for
  /budget                  — show remaining USDC spend budget
  /price symbol:<ticker>   — direct live price (CoinGecko), $0.001
  /weather city:<city>     — direct live weather (Open-Meteo), $0.001
  /gpt prompt:<text>       — direct premium answer (Claude), $0.01
  /ping                    — x402 smoke test
  /nanopay-info            — about the bot
"""

from __future__ import annotations

import asyncio
import logging

import discord
import httpx
from discord import app_commands

from ..agent import planner
from ..payments.config import (
    API_BASE_URL,
    DEFAULT_PRICE_ATOMIC,
    DISCORD_BOT_TOKEN,
)
from .payer import build_paying_client, pay_and_execute

logger = logging.getLogger("nanopay.bot")


# ============================================================================
# Bot client
# ============================================================================


class NanoPayBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        # plain client for /payments/create and /status — no payment needed
        self._api: httpx.AsyncClient | None = None
        # x402-paying client — auto-handles 402 and retries with EIP-3009 sig
        self._payer: httpx.AsyncClient | None = None

    async def setup_hook(self) -> None:
        self._api = httpx.AsyncClient(base_url=API_BASE_URL, timeout=30)
        self._payer = build_paying_client()
        await self.tree.sync()
        logger.info("Slash commands synced globally")

    async def on_ready(self) -> None:
        logger.info("NanoPay bot ready as %s", self.user)
        for guild in self.guilds:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Commands synced to guild %s (%s)", guild.name, guild.id)

    async def close(self) -> None:
        if self._api:
            await self._api.aclose()
        if self._payer:
            await self._payer.aclose()
        await super().close()


bot = NanoPayBot()


# ============================================================================
# Helpers
# ============================================================================


async def _create_payment(
    guild_id: str,
    channel_id: str,
    user_id: str,
    command_name: str,
    command_args: dict[str, str],
    interaction_token: str,
    application_id: str,
    price_atomic: int = DEFAULT_PRICE_ATOMIC,
) -> dict[str, str]:
    assert bot._api is not None
    resp = await bot._api.post(
        "/payments/create",
        json={
            "guild_id": guild_id,
            "channel_id": channel_id,
            "user_id": user_id,
            "command_name": command_name,
            "command_args": command_args,
            "price_atomic": price_atomic,
            "interaction_token": interaction_token,
            "application_id": application_id,
        },
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def _get_budget(user_id: str) -> dict[str, int]:
    assert bot._api is not None
    resp = await bot._api.get(f"/budget/{user_id}")
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


def _price_display(atomic: int) -> str:
    return f"${atomic / 1_000_000:.4f}"


def _result_embed(command_name: str, result: str, tx: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"/{command_name}",
        description=result,
        color=0x22C55E,
    )
    if tx:
        embed.add_field(
            name="Arc tx",
            value=f"[{tx[:16]}...](https://explorer.testnet.arc.network/tx/{tx})",
            inline=False,
        )
    embed.set_footer(text="Paid via x402 on Arc Testnet • NanoPay for Discord")
    return embed


async def _handle_premium_command(
    interaction: discord.Interaction,
    command_name: str,
    args: dict[str, str],
    price_atomic: int = DEFAULT_PRICE_ATOMIC,
) -> None:
    """Handle a premium slash command with autonomous x402 payment."""
    await interaction.response.defer(ephemeral=True)

    guild_id = str(interaction.guild_id or "dm")
    channel_id = str(interaction.channel_id or "")
    user_id = str(interaction.user.id)

    # Register payment record
    try:
        data = await _create_payment(
            guild_id=guild_id,
            channel_id=channel_id,
            user_id=user_id,
            command_name=command_name,
            command_args=args,
            interaction_token=interaction.token,
            application_id=str(interaction.application_id),
            price_atomic=price_atomic,
        )
    except Exception as exc:
        await interaction.followup.send(f"Could not initialise payment: {exc}", ephemeral=True)
        return

    payment_id = data["payment_id"]
    pay_url = data["pay_url"]
    price_str = _price_display(price_atomic)

    # Mode A: bot pays autonomously
    assert bot._payer is not None
    try:
        result_data = await pay_and_execute(bot._payer, payment_id)
        tx = result_data.get("tx_hash", "")
        result = result_data.get("result", "(no result)")
        await interaction.followup.send(
            embed=_result_embed(command_name, result, tx),
            ephemeral=True,
        )
        return
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 402:
            # Unexpected error — surface it
            await interaction.followup.send(
                f"Payment error ({exc.response.status_code}): {exc.response.text[:200]}",
                ephemeral=True,
            )
            return
        # 402 even after auto-pay attempt — fall through to Mode B
        logger.warning("Auto-pay failed (402 after retry), falling back to MetaMask: %s", exc)
    except Exception as exc:
        logger.warning("Auto-pay error, falling back to MetaMask: %s", exc)

    # Mode B: manual MetaMask fallback
    embed = discord.Embed(
        title=f"/{command_name} — pay {price_str} USDC",
        description=(
            "Bot wallet insufficient. Click below to pay via MetaMask on Arc Testnet.\n"
            "Result will appear here once confirmed."
        ),
        color=0xF59E0B,
    )
    embed.add_field(name="Amount", value=price_str, inline=True)
    embed.add_field(name="Network", value="Arc Testnet", inline=True)
    embed.set_footer(text="x402 EIP-3009 • NanoPay for Discord")

    view = _PayView(pay_url)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # Poll for manual payment completion
    asyncio.create_task(_poll_and_deliver(interaction, payment_id, command_name))


async def _handle_agent_request(interaction: discord.Interaction, prompt: str) -> None:
    """The agentic path: the agent decides whether to spend, on which tool, within budget."""
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)

    try:
        budget = await _get_budget(user_id)
    except Exception as exc:
        await interaction.followup.send(f"Could not read budget: {exc}", ephemeral=True)
        return
    remaining = budget["remaining_atomic"]

    decision = await planner.decide(prompt, remaining)

    # No spend needed — the agent answers for free.
    if decision.action == "answer_free":
        answer = await planner.answer_free(prompt)
        embed = discord.Embed(title="Agent answer (free)", description=answer, color=0x60A5FA)
        embed.add_field(name="Decision", value=decision.reason, inline=False)
        embed.add_field(name="Spent", value="$0.0000", inline=True)
        embed.add_field(name="Budget left", value=_price_display(remaining), inline=True)
        embed.set_footer(text="NanoPay agent • no payment needed")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # Over budget — the agent declines.
    if decision.action == "decline":
        embed = discord.Embed(
            title="Agent declined to spend", description=decision.reason, color=0xF59E0B
        )
        embed.add_field(name="Budget left", value=_price_display(remaining), inline=True)
        embed.set_footer(text="NanoPay agent • spend governance")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    # Pay: the agent buys the chosen tool over x402, then composes the answer.
    assert decision.tool is not None
    tool = decision.tool
    arg_val = decision.args.get(tool.arg_name) or prompt
    try:
        data = await _create_payment(
            guild_id=str(interaction.guild_id or "dm"),
            channel_id=str(interaction.channel_id or ""),
            user_id=user_id,
            command_name=tool.command,
            command_args={tool.arg_name: arg_val},
            interaction_token=interaction.token,
            application_id=str(interaction.application_id),
            price_atomic=tool.price_atomic,
        )
        result_data = await pay_and_execute(bot._payer, data["payment_id"])  # type: ignore[arg-type]
    except Exception as exc:
        logger.warning("agent pay/execute failed: %s", exc)
        await interaction.followup.send(f"Agent payment failed: {exc}", ephemeral=True)
        return

    tx = result_data.get("tx_hash", "")
    tool_result = result_data.get("result", "(no result)")
    final = await planner.compose(prompt, tool.name, tool_result)

    spent_after = budget["spent_atomic"] + tool.price_atomic
    left_after = max(0, budget["limit_atomic"] - spent_after)

    embed = discord.Embed(title="Agent answer", description=final, color=0x22C55E)
    embed.add_field(name="Decision", value=f"Paid {tool.name} ({tool.price_display})", inline=False)
    embed.add_field(name="Spent now", value=tool.price_display, inline=True)
    embed.add_field(name="Budget left", value=_price_display(left_after), inline=True)
    if tx:
        embed.add_field(
            name="Arc receipt",
            value=f"[{tx[:16]}...](https://testnet.arcscan.app/tx/{tx})",
            inline=False,
        )
    embed.set_footer(text="Paid via x402 on Arc Testnet • NanoPay agent")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def _poll_and_deliver(
    interaction: discord.Interaction,
    payment_id: str,
    command_name: str,
    max_seconds: int = 600,
) -> None:
    """Background task: poll until paid, then post result (MetaMask fallback)."""
    assert bot._api is not None
    deadline = asyncio.get_event_loop().time() + max_seconds
    while asyncio.get_event_loop().time() < deadline:
        try:
            resp = await bot._api.get(f"/status/{payment_id}")
            d = resp.json()
            if d.get("status") == "paid":
                await interaction.followup.send(
                    embed=_result_embed(
                        command_name,
                        d.get("result", "(no result)"),
                        d.get("tx_hash", ""),
                    ),
                    ephemeral=True,
                )
                return
        except Exception as exc:
            logger.debug("Poll error: %s", exc)
        await asyncio.sleep(3)

    await interaction.followup.send(
        "Payment timed out. Run the command again to retry.",
        ephemeral=True,
    )


class _PayView(discord.ui.View):
    def __init__(self, pay_url: str) -> None:
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Pay with MetaMask",
                url=pay_url,
                style=discord.ButtonStyle.link,
                emoji="💜",
            )
        )


# ============================================================================
# Slash commands
# ============================================================================


@bot.tree.command(name="weather", description="Current weather — $0.01 USDC via x402")
@app_commands.describe(city="City name")
async def cmd_weather(interaction: discord.Interaction, city: str) -> None:
    await _handle_premium_command(interaction, "weather", {"city": city})


@bot.tree.command(name="price", description="Crypto price — $0.01 USDC via x402")
@app_commands.describe(symbol="Token symbol e.g. BTC, ETH")
async def cmd_price(interaction: discord.Interaction, symbol: str) -> None:
    await _handle_premium_command(interaction, "price", {"symbol": symbol})


@bot.tree.command(
    name="ask", description="Ask the agent — it decides what (if anything) to pay for"
)
@app_commands.describe(prompt="Anything. The agent picks a paid tool only if it helps.")
async def cmd_ask(interaction: discord.Interaction, prompt: str) -> None:
    await _handle_agent_request(interaction, prompt)


@bot.tree.command(name="budget", description="Show your remaining USDC spend budget")
async def cmd_budget(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    budget = await _get_budget(str(interaction.user.id))
    embed = discord.Embed(title="Your NanoPay budget", color=0x7C3AED)
    embed.add_field(name="Limit", value=_price_display(budget["limit_atomic"]), inline=True)
    embed.add_field(name="Spent", value=_price_display(budget["spent_atomic"]), inline=True)
    embed.add_field(name="Left", value=_price_display(budget["remaining_atomic"]), inline=True)
    embed.set_footer(text="The agent will not spend past your limit.")
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="gpt", description="Direct Claude answer — $0.01 USDC via x402")
@app_commands.describe(prompt="Your question")
async def cmd_gpt(interaction: discord.Interaction, prompt: str) -> None:
    await _handle_premium_command(interaction, "ask", {"prompt": prompt})


@bot.tree.command(name="ping", description="x402 smoke test — bot pays autonomously")
async def cmd_ping(interaction: discord.Interaction) -> None:
    await _handle_premium_command(interaction, "ping", {})


# ============================================================================
# Info command
# ============================================================================


@bot.tree.command(name="nanopay-info", description="About NanoPay for Discord")
async def cmd_info(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="NanoPay — the Discord agent that pays its own way",
        description=(
            "Ask for something behind a paywall. The agent pays an x402 service "
            "in USDC on Arc from its own wallet, settles on-chain, and returns the "
            "result. No MetaMask, no human wallet step.\n\n"
            "The agent signs an EIP-3009 authorization (USDC only, no gas). The "
            "service relays it on-chain and pays the gas. Two distinct wallets, so "
            "USDC actually moves."
        ),
        color=0x7C3AED,
    )
    embed.add_field(name="Network", value="Arc Testnet (eip155:5042002)", inline=False)
    embed.add_field(name="Protocol", value="x402 exact — EIP-3009 USDC", inline=False)
    embed.add_field(name="Default price", value="$0.01 USDC per call", inline=False)
    embed.set_footer(text="NanoPay — Lepton Agents Hackathon 2026")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ============================================================================
# Entry point
# ============================================================================


def run() -> None:
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set. Add it to .env")
    discord.utils.setup_logging(level=logging.INFO)
    bot.run(DISCORD_BOT_TOKEN)
