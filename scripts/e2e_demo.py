"""End-to-end proof: the agent pays an x402 service in USDC on Arc and settles.

Requires the API server running (make api) and AGENT_PRIVATE_KEY funded
(scripts/fund_agent.py). Creates a payment, has the agent autonomously pay and
execute it, then verifies the settlement transaction on Arc.

Usage:
  .venv/bin/python scripts/e2e_demo.py [command] [arg]
  e.g. .venv/bin/python scripts/e2e_demo.py price BTC
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from eth_account import Account
from web3 import Web3

from src.bot.payer import build_paying_client, pay_and_execute
from src.payments.config import (
    AGENT_PRIVATE_KEY,
    API_BASE_URL,
    ARC_RPC_URL,
    SELLER_WALLET_ADDRESS,
)


async def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "ping"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    arg_name = {"price": "symbol", "weather": "city", "ask": "prompt"}.get(command, "")
    args = {arg_name: arg} if arg_name and arg else {}
    price = {"price": 1000, "weather": 1000, "ask": 10000}.get(command, 1000)

    agent = Account.from_key(AGENT_PRIVATE_KEY)
    w3 = Web3(Web3.HTTPProvider(ARC_RPC_URL))
    before = w3.eth.get_balance(agent.address)
    print(f"Agent {agent.address} -> Service {SELLER_WALLET_ADDRESS}")
    print(f"Agent USDC before: {before / 1e18:.6f}")

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30) as api:
        resp = await api.post(
            "/payments/create",
            json={
                "user_id": "e2e-demo",
                "command_name": command,
                "command_args": args,
                "price_atomic": price,
            },
        )
        resp.raise_for_status()
        payment_id = resp.json()["payment_id"]
        print(f"Created payment {payment_id} for /{command} at {price} atomic")

    payer = build_paying_client()
    try:
        result = await pay_and_execute(payer, payment_id)
    finally:
        await payer.aclose()

    tx = result.get("tx_hash", "")
    print(f"Result: {result.get('result')}")
    print(f"Settlement tx: {tx}")
    print(f"Payer (recovered): {result.get('payer')}")

    if tx:
        rec = w3.eth.get_transaction_receipt(tx)
        print(f"On-chain status: {rec.status} in block {rec.blockNumber}")
    after = w3.eth.get_balance(agent.address)
    print(f"Agent USDC after: {after / 1e18:.6f} (delta {(after - before) / 1e18:+.6f})")
    print(f"Explorer: https://testnet.arcscan.app/tx/{tx}")


if __name__ == "__main__":
    asyncio.run(main())
