# NanoPay

The Discord agent that pays its own way on Arc.

You ask it something. It decides whether it needs a paid service, pays for that
service in USDC over x402 on Arc from its own wallet, settles on-chain in under a
second, and hands you the answer plus the receipt. No MetaMask, no human in the
signing loop, no subscription.

Built for the Lepton Agents Hackathon (Canteen x Circle), RFB-1: Autonomous
Paying Agents.

## The gap this fills

Almost every x402-on-Arc project so far is a seller: a paywall, a creator payout,
a way to charge for content. The buyer side is empty. A GitHub search for
`discord x402 arc` returns nothing. NanoPay is the buyer: an autonomous agent
that spends, not an API that charges.

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
(status 1). Reproduce with `scripts/e2e_demo.py`.

| What | Tx | Result |
|------|----|--------|
| Fund agent wallet | [`bcaf7e00…`](https://testnet.arcscan.app/tx/bcaf7e00c11f6e83dc87e86dbd75da4d34f7f40514e3424b63be8cf2d248efc6) | agent funded 0.05 USDC |
| `/ping` (smoke) | [`a7afaefc…`](https://testnet.arcscan.app/tx/a7afaefc0c810473fd41ad605473bec33df32f02d21888f808276ef4f21c9a3b) | settled, −$0.001 |
| `/price BTC` (real CoinGecko) | [`ab04f45f…`](https://testnet.arcscan.app/tx/ab04f45fc9949368b01b1bb7666c889e9e18beff1b81675b9840a7bba36b45b2) | `BTC = $63,179 (-1.06% 24h)` |
| `/weather Tokyo` (real Open-Meteo) | [`4e54d2cc…`](https://testnet.arcscan.app/tx/4e54d2cc9b560b2a1418e84f9b86004514d30c5580bd119fd3776637cd3fa931) | `Tokyo, JP: 22.0°C, overcast` |
| `/ask` full loop (agent decides → pays → composes) | [`98a743b4…`](https://testnet.arcscan.app/tx/98a743b4ebf8c6f7a59bfb4b32aa7df259d6d98e898fd2db9194e071be3e2c13) | agent paid CoinGecko, answered `BTC $63,034 -1.29%` |

- Agent (payer): `0x6a1b4267921f41f9D5D1FACF998Da9BB930701c4`
- Service (payTo): `0xDB6c6340342e71A63cD11Ebac2185204b7777777`

Detail worth noticing: the agent's on-chain transaction count is **0**. It paid
three times and never sent a transaction, because EIP-3009 lets it sign an
authorization off-chain while the facilitator broadcasts and pays the gas. That
is the whole point of the "exact" scheme: the payer needs USDC, nothing else.

## How it maps to the judging

- **Agentic sophistication.** The agent decides. Given a free-form request it
  reasons over a priced tool catalog, picks the single cheapest tool that helps
  (or none), and refuses to spend past a budget. It is not a fixed
  `/price -> CoinGecko` script. See `/ask` declining an over-budget call.
- **Traction.** Every call is a real on-chain USDC settlement on Arc, so usage is
  provable, not claimed. The table above is reproducible by anyone with the repo.
- **Circle tooling.** x402 HTTP 402 exact scheme, EIP-3009 USDC on Arc, an
  embedded facilitator that settles in-process, USDC-as-gas. Optional Circle
  developer-controlled wallet path is wired in config.
- **Innovation.** Buyer-side autonomous agent on Discord, the empty quadrant, plus
  a spend-governance layer (per-user USDC budgets, decline path, on-chain receipt)
  that turns a pay-per-call toy into a shared team utility.

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

`pytest` is green (31 tests) and `mypy --strict` is clean. Network calls are
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
