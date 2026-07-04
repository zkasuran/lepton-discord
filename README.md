# NanoPay

The Discord agent that pays its own way on Arc.

You ask it something. It decides whether it needs a paid service, pays for that
service in USDC over x402 on Arc from its own wallet, settles on-chain in under a
second, and hands you the answer plus the receipt. No MetaMask, no human in the
signing loop, no subscription.

Built for the Lepton Agents Hackathon (Canteen x Circle), RFB-1: Autonomous
Paying Agents.

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
        │
        ▼
  NanoPay agent (Claude)  ── decides: "I need a live price, that costs $0.001"
        │                         checks budget, picks the cheapest tool
        ▼
  x402 service on Arc  ◄── 402 challenge ── agent signs EIP-3009 (USDC, no gas)
        │                  facilitator settles on Arc in <500ms, pays the gas
        ▼
  real CoinGecko price ──► agent composes the answer
        │
        ▼
  Discord:  answer + "paid $0.001 • budget left $0.049 • Arc receipt 0xab04…"
```

The agent's reasoning is free (it runs on the operator's model). It spends USDC
only on the external tools it decides are worth buying.

## Live proof (Arc testnet, verified on-chain)

Two distinct wallets, real USDC moving between them, every settlement confirmed
(status 1). These are a fresh in-window run (2026-07-04). Reproduce with
`scripts/e2e_demo.py` and `scripts/agent_demo.py`.

| What | Tx | Result |
|------|----|--------|
| `/ping` (smoke) | [`48d60980…`](https://testnet.arcscan.app/tx/48d60980cb6a5da0ca7350f234b34250ec2001ad594a1ca4ceb232aaf1a039a7) | settled, −$0.001 |
| `/price BTC` (real CoinGecko) | [`ad1f0d04…`](https://testnet.arcscan.app/tx/ad1f0d044f28535353b9d981293ad12e2f02da583d42374630cce3e6a3057c67) | `BTC = $62,498 (+1.85% 24h)` |
| `/weather Tokyo` (real Open-Meteo) | [`59ab0652…`](https://testnet.arcscan.app/tx/59ab065209b8f5d4c7cf871aaa4528ab4416d23259ca70f478bbe11abe3c13af) | `Tokyo, JP: 23.3°C, partly cloudy` |
| `/ask` full loop (agent decides → pays → composes) | [`70ca2d2a…`](https://testnet.arcscan.app/tx/70ca2d2aa7e6ff894484d0f6a4f910c4f1e89a3685bb5da30374e42bf75edd2f) | agent chose to pay CoinGecko, answered `BTC $62,486 +1.83%` |

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
pays for paywalled APIs on a budget, without overspending. The RFB's own example
is "BudgetBot, $10/day budget across APIs". That is what this is, on Discord.

- **Agentic sophistication.** The agent decides. Given a free-form request it
  reasons over a priced tool catalog, picks the single cheapest tool that helps
  (or none), and refuses to spend past a budget. It is not a fixed
  `/price -> CoinGecko` script. See `/ask` declining an over-budget call. The
  budget is enforced in code after the model picks, so the model cannot overspend.
- **Traction.** Mapped to RFB-1's own metrics. Total autonomous payments: every
  `/ask` that needs data is one. Average transaction size: $0.001, sub-cent, the
  stated target. Budget efficiency: over-budget calls are declined, not paid.
  Cost per task: one sub-cent settlement per answered request. Every call is a
  real on-chain USDC settlement on Arc (table above), provable not claimed.
- **Circle tooling.** x402 HTTP 402 exact scheme, EIP-3009 USDC on Arc, an
  embedded facilitator that settles in-process, USDC-as-gas. Optional Circle
  developer-controlled wallet path is wired in config.
- **Innovation.** An autonomous payer inside a shared social channel, the surface
  no rival holds (a `discord x402 arc` repo search returns nothing). Buyer-side
  agents on Arc exist (Keryx is the sharp one), but they are single-user web or
  CLI. NanoPay's spend-governance is per-user, so one agent serves a whole channel:
  many people, separate USDC budgets each. This is agentic commerce where the users
  already are, not another dashboard.

## Commands

| Command | What it does | Cost |
|---------|--------------|------|
| `/ask <prompt>` | The agent decides what, if anything, to buy, then answers | $0 to $0.01 |
| `/budget` | Show your remaining USDC spend budget | free |
| `/price <symbol>` | Direct live price (CoinGecko) | $0.001 |
| `/weather <city>` | Direct live weather (Open-Meteo) | $0.001 |
| `/gpt <prompt>` | Direct premium answer (Claude, server-side) | $0.01 |
| `/ping` | x402 smoke test | $0.001 |
| `/nanopay-info` | About the bot | free |

## Architecture

Hexagonal: domain models hold no IO, ports define the seams, adapters do the work.

- `src/agent/` — the brain. `planner.decide()` chooses a tool and enforces the
  budget in code (not left to the model); `tools.py` is the priced catalog.
- `src/bot/` — the Discord client and `payer.py`, the x402 client that signs
  EIP-3009 from the agent wallet.
- `src/api/` — FastAPI resource server. `/execute/{id}` is the x402-gated
  endpoint; `executor.py` runs the real services (CoinGecko, Open-Meteo, Claude).
- `src/payments/` — config, the embedded x402 facilitator, the SQLite store.

Two wallets, on purpose: the agent (payer) is separate from the service (payTo and
facilitator), so USDC actually moves between parties instead of round-tripping to
itself.

Stack: `x402==2.13` (exact EVM scheme), `discord.py`, `fastapi`, `eth-account`,
`anthropic`, `aiosqlite`, Arc testnet (`eip155:5042002`), USDC system contract
`0x3600000000000000000000000000000000000000` (verified `name()="USDC"`,
`version()="2"`, the values the EIP-712 domain must match or settlement reverts).

## Setup

```bash
make dev-install            # uv venv + deps
cp .env.example .env        # then fill in the values below
make lint                   # ruff + ruff format + mypy --strict
make test                   # pytest
```

Required env: `DISCORD_BOT_TOKEN`, `ANTHROPIC_API_KEY`, an `AGENT_PRIVATE_KEY`
(the payer, holds USDC), a `SELLER_WALLET_ADDRESS` and `FACILITATOR_PRIVATE_KEY`
(the service, receives USDC and relays settlement). See `.env.example`.

## Run

```bash
make api                                  # start the x402 resource server
.venv/bin/python scripts/fund_agent.py    # generate + fund the agent wallet
.venv/bin/python scripts/e2e_demo.py price BTC   # one real paid call, end to end
make bot                                  # start the Discord bot
```

Fund the agent wallet with testnet USDC from https://faucet.circle.com (Arc
Testnet, 20 USDC per address per 2h), or from another funded wallet.

## Tests

`pytest` is green (32 tests) and `mypy --strict` is clean. Network calls are
mocked, so the suite is deterministic and offline. The on-chain settlements above
were run separately against live Arc testnet.

## Security

Testnet only. Never commit a real key. `.env` is gitignored; `.env.example`
ships placeholders. The agent wallet in the proof table is a throwaway testnet
wallet.

## AI disclosure

This project was built with substantial help from Claude (Anthropic), which
drafted the agent decision layer, the executors, the wallet-split refactor and
this README. Every change was verified locally before commit: `ruff`,
`ruff format --check`, `mypy --strict` and `pytest` all pass, and the payment
loop was run end-to-end on Arc testnet with the transactions linked above. Design
choices are the author's and are documented here for review.
