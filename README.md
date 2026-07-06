# NanoPay

**The Discord agent that pays its own way on Arc.**

You ask it something. It decides whether it needs a paid service, pays for that
service in USDC over x402 on Arc from its own wallet, settles on-chain in under a
second, and hands you the answer plus the receipt. No wallet, no signing, no human
in the loop, no subscription.

Built for the Lepton Agents Hackathon (Canteen x Circle), RFB-1: Autonomous Paying
Agents.

[![live site](https://img.shields.io/badge/live-zkasuran.github.io%2Flepton--discord-0f8a56)](https://zkasuran.github.io/lepton-discord/)
[![settles on Arc](https://img.shields.io/badge/settles_on-Arc_testnet-1f1f1f)](https://docs.arc.network)
[![payments x402](https://img.shields.io/badge/payments-x402_%2B_EIP--3009-2775CA)](https://github.com/circlefin/arc-nanopayments)
[![tests](https://img.shields.io/badge/tests-57_passing-0f8a56)](#tests)

## Links

- **Landing page (live):** https://zkasuran.github.io/lepton-discord/
- **Join the demo server** (bot is already there, run `/ask` in `#general`):
  https://discord.gg/JST4tjKWz
- **Add NanoPay to your own server** (public bot, syncs its commands instantly):
  https://discord.com/oauth2/authorize?client_id=1517400111699726488&permissions=18432&scope=bot+applications.commands
- **Source:** https://github.com/zkasuran/lepton-discord

## Try it right now

The bot is deployed and always on, so you do not need to run anything. Two ways:

1. **Join the demo server** (fastest): https://discord.gg/JST4tjKWz then run `/ask`
   in `#general`.
2. **Add it to your own server**: use the invite link above. It is a public bot,
   so it syncs its 8 commands to your server in seconds. Same agent, many servers.

Then:

- `/ask what's BTC doing right now, one line` -> the agent decides it needs a live
  price, pays a sub-cent USDC toll on Arc, and answers with a clickable receipt.
- `/ask explain the TCP handshake` -> it answers for free, spending $0.
- `/budget` -> your per-user USDC budget.

The landing page shows the same loop with an animated demo, a live "Ask the agent"
box, the real on-chain settlements below, and a judges FAQ.

## The gap this fills

Almost every x402-on-Arc project is single-user: a website or a CLI, one wallet,
one person. Buyer-side agents exist now (Keryx settles citation tolls on Arc), but
they run one user at a time behind a web UI. Nobody has put an autonomous payer in
the place communities actually live. A GitHub search for `discord x402 arc`
returns nothing. NanoPay is that: an autonomous buyer inside a shared Discord
channel, many distinct users each spending under their own USDC budget.

## The loop

```
Discord user:  /ask "what's BTC doing and is it a good week to care?"
        |
        v
  NanoPay agent  ── decides: "I need a live price, that costs $0.001"
        |                     checks budget, picks the cheapest tool
        v
  x402 service on Arc  <── 402 challenge ── agent signs EIP-3009 (USDC, no gas)
        |                   facilitator settles on Arc in <500ms, pays the gas
        v
  real CoinGecko price ──> agent composes the answer
        |
        v
  Discord:  answer + "paid $0.001 · budget left $0.499 · Arc receipt 0xad1f…"
```

The agent's reasoning is free (it runs on the operator's model). It spends USDC
only on the external tools it decides are worth buying.

## Live proof (Arc testnet, verified on-chain)

Two distinct wallets, real USDC moving between them, every settlement confirmed
(status 1). Reproduce with `scripts/e2e_demo.py` and `scripts/agent_demo.py`, or
just run `/ask` in Discord.

| What | Tx | Result |
|------|----|--------|
| `/ask` full loop (agent decides, pays, composes) | [`70ca2d2a…`](https://testnet.arcscan.app/tx/0x70ca2d2aa7e6ff894484d0f6a4f910c4f1e89a3685bb5da30374e42bf75edd2f) | agent chose to pay CoinGecko, answered `BTC $62,486 +1.83%` |
| `/ask` on the OpenAI-compatible model | [`2dd13f99…`](https://testnet.arcscan.app/tx/0x2dd13f99b06cdf9a43a71a618eb31bde76380541f06f662ad6cab156db6343b0) | agent chose to pay, answered `BTC $62,703 +0.09%` |
| `/price BTC` (real CoinGecko) | [`ad1f0d04…`](https://testnet.arcscan.app/tx/0xad1f0d044f28535353b9d981293ad12e2f02da583d42374630cce3e6a3057c67) | `BTC = $62,498 (+1.85% 24h)` |
| `/weather Tokyo` (real Open-Meteo) | [`59ab0652…`](https://testnet.arcscan.app/tx/0x59ab065209b8f5d4c7cf871aaa4528ab4416d23259ca70f478bbe11abe3c13af) | `Tokyo, JP: 23.3°C, partly cloudy` |
| `/ping` (smoke) | [`48d60980…`](https://testnet.arcscan.app/tx/0x48d60980cb6a5da0ca7350f234b34250ec2001ad594a1ca4ceb232aaf1a039a7) | settled, −$0.001 |

- Agent (payer): `0x6a1b4267921f41f9D5D1FACF998Da9BB930701c4`
- Service (payTo): `0xDB6c6340342e71A63cD11Ebac2185204b7777777`

Detail worth noticing: the agent's on-chain transaction count is **0**. It paid
every time and never sent a transaction, because EIP-3009 lets it sign an
authorization off-chain while the facilitator broadcasts and pays the gas. On
every receipt above the `from` is the facilitator `0xDB6c…`, not the agent, and
the `to` is the USDC contract `0x3600…`. That is the whole point of the "exact"
scheme: the payer needs USDC, nothing else.

The three agency branches are reproducible too: `pay` (buys the priced tool),
`answer_free` (no tool needed, $0 spent) and `decline` (over the per-user budget,
enforced in code, not by the model).

## How it maps to the judging

This is RFB-1, Autonomous Paying Agents: an agent that discovers, evaluates and
pays for paywalled APIs on a budget, without overspending.

- **Agentic sophistication.** The agent decides. Given a free-form request it
  reasons over a priced tool catalog, picks the single cheapest tool that helps
  (or none), and refuses to spend past a budget. It is not a fixed
  `/price -> CoinGecko` script. The budget is enforced in code after the model
  picks, so the model cannot overspend.
- **Traction.** Every `/ask` that needs data is a real sub-cent USDC settlement on
  Arc, provable not claimed. The bot is live 24/7 so usage is real, and the
  numbers are computed from the payment store by `scripts/traction_report.py`.
- **Circle tooling.** x402 HTTP 402 exact scheme, EIP-3009 USDC on Arc, an
  embedded facilitator that settles in-process, USDC-as-gas, and the two-wallet
  split that makes each settlement a real transfer between distinct parties.
- **Innovation.** An autonomous payer inside a shared social channel, the surface
  no rival holds. Buyer-side agents on Arc exist (Keryx is the sharp one), but they
  are single-user web or CLI. NanoPay's spend governance is per-user, so one agent
  serves a whole channel: many people, separate USDC budgets each.

## Commands

| Command | What it does | Cost |
|---------|--------------|------|
| `/ask <prompt>` | The agent decides what, if anything, to buy, then answers | $0 to $0.01 |
| `/budget` | Show your remaining USDC spend budget | free |
| `/price <symbol>` | Direct live price (CoinGecko) | $0.001 |
| `/weather <city>` | Direct live weather (Open-Meteo) | $0.001 |
| `/news <topic>` | Latest headlines on any topic (Google News) | $0.001 |
| `/gpt <prompt>` | Direct premium answer (the model, server-side) | $0.01 |
| `/ping` | x402 smoke test | $0.001 |
| `/nanopay-info` | About the bot | free |

## Architecture

Hexagonal: domain models hold no IO, ports define the seams, adapters do the work.

- `src/agent/`: the brain. `planner.decide()` chooses a tool and enforces the
  budget in code (not left to the model); `tools.py` is the priced catalog;
  `llm.py` is the model provider layer.
- `src/bot/`: the Discord client and `payer.py`, the x402 client that signs
  EIP-3009 from the agent wallet.
- `src/api/`: FastAPI resource server. `/execute/{id}` is the x402-gated endpoint;
  `executor.py` runs the real services; `/demo/ask` is the public browser demo.
- `src/payments/`: config, the embedded x402 facilitator, the SQLite store, and
  the traction summary.

Two wallets, on purpose: the agent (payer) is separate from the service (payTo and
facilitator), so USDC actually moves between parties instead of round-tripping to
itself.

Stack: `x402==2.13` (exact EVM scheme), `discord.py`, `fastapi`, `eth-account`,
`aiosqlite`, Arc testnet (`eip155:5042002`), USDC system contract
`0x3600000000000000000000000000000000000000` (verified `name()="USDC"`,
`version()="2"`, the values the EIP-712 domain must match or settlement reverts).

## The model provider

The agent's reasoning runs on Anthropic **or** any OpenAI-compatible endpoint,
selected by env (`src/agent/llm.py`). Set `OPENAI_BASE_URL` + `OPENAI_API_KEY` for
an OpenAI-compatible model, or `ANTHROPIC_API_KEY` for Claude, or force one with
`LLM_PROVIDER`. Tool-use maps to the same decide-or-answer-free decision either
way. Reasoning is free; USDC only ever pays for the external tools.

## Public browser demo

`POST /demo/ask` runs the real agent loop from a browser and returns the decision,
answer, spend and a fresh Arc tx, so the landing page can settle a real payment per
ask. It is rate-limited per IP with a global spend cap so public traffic can never
drain the agent wallet, and CORS is opened for the static site.

## Setup

```bash
make dev-install            # uv venv + deps
cp .env.example .env        # then fill in the values
make lint                   # ruff + ruff format + mypy --strict
make test                   # pytest
```

Required env: `DISCORD_BOT_TOKEN`, a model provider (`OPENAI_*` or
`ANTHROPIC_API_KEY`), an `AGENT_PRIVATE_KEY` (the payer, holds USDC), a
`SELLER_WALLET_ADDRESS` and a facilitator key (the service, receives USDC and
relays settlement). Optional `GUILD_ID` syncs slash commands to your server
instantly. See `.env.example`.

## Run

```bash
make api                                  # start the x402 resource server
.venv/bin/python scripts/fund_agent.py    # generate + fund the agent wallet
.venv/bin/python scripts/agent_demo.py "price of BTC?"   # one real agentic call
make bot                                  # start the Discord bot
```

Fund the agent wallet with testnet USDC from https://faucet.circle.com (Arc
Testnet), or from another funded wallet.

If a slash command does not appear, force a guild sync (instant) without a
restart: `.venv/bin/python scripts/sync_commands.py <guild_id>`.

## Deploy (always on)

The bot dials out to Discord, so it needs no inbound ports. Run the API and the
bot as services so they survive reboots. The repo includes systemd-ready entry
points (`run_api.py`, `run_bot.py`); point two `systemd` units at
`/.venv/bin/python run_api.py` and `run_bot.py` with `Restart=on-failure`. The
production instance runs exactly this, which is why the bot is live 24/7.

## Tests

`pytest` is green (57 tests) and `mypy --strict` is clean. Network and model calls
are mocked, so the suite is deterministic and offline. The on-chain settlements
above were run separately against live Arc testnet.

## Roadmap

An open agent-to-agent marketplace: any member lists a priced service, an admin
verifies it is legit, and the agent can then discover and pay for it. NanoPay
becomes both the buyer and the marketplace, per channel.

## Security

Testnet only. Never commit a real key. `.env` is gitignored; `.env.example` ships
placeholders. The agent wallet in the proof table is a throwaway testnet wallet.

## AI disclosure

This project was built with substantial help from Claude (Anthropic), which drafted
the agent decision layer, the model provider, the executors, the deploy and this
README. Every change was verified before commit: `ruff`, `ruff format --check`,
`mypy --strict` and `pytest` all pass, and the payment loop was run end-to-end on
Arc testnet with the transactions linked above. The author reviewed the work and
owns the design choices.
