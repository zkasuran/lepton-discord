"""Full agentic loop: decide -> pay x402 on Arc -> compose. Mirrors /ask.

Requires the API server running and AGENT_PRIVATE_KEY funded.

Usage:
  .venv/bin/python scripts/agent_demo.py "what's the price of BTC right now?"
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from web3 import Web3

from src.agent import planner
from src.bot.payer import build_paying_client, pay_and_execute
from src.payments.config import API_BASE_URL, ARC_RPC_URL


async def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "what's the price of BTC right now?"
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 50000

    print(f"User: {prompt}")
    decision = await planner.decide(prompt, budget)
    print(f"Agent decision: {decision.action} ({decision.reason})")

    if decision.action == "answer_free":
        answer = await planner.answer_free(prompt)
        print(f"Answer (free): {answer[:300]}")
        return
    if decision.action == "decline":
        print("Agent declined to spend.")
        return

    assert decision.tool is not None
    tool = decision.tool
    arg_val = decision.args.get(tool.arg_name) or prompt

    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30) as api:
        resp = await api.post(
            "/payments/create",
            json={
                "user_id": "agent-demo",
                "command_name": tool.command,
                "command_args": {tool.arg_name: arg_val},
                "price_atomic": tool.price_atomic,
            },
        )
        resp.raise_for_status()
        payment_id = resp.json()["payment_id"]

    payer = build_paying_client()
    try:
        result = await pay_and_execute(payer, payment_id)
    finally:
        await payer.aclose()

    tx = result.get("tx_hash", "")
    tool_result = result.get("result", "")
    print(f"Paid {tool.name} ({tool.price_display}) -> {tool_result}")
    print(f"Arc tx: {tx}")
    if tx:
        w3 = Web3(Web3.HTTPProvider(ARC_RPC_URL))
        rec = w3.eth.get_transaction_receipt(tx)
        print(f"On-chain status: {rec.status} in block {rec.blockNumber}")
    final = await planner.compose(prompt, tool.name, tool_result)
    print(f"Agent answer: {final[:400]}")
    print(f"Explorer: https://testnet.arcscan.app/tx/{tx}")


if __name__ == "__main__":
    asyncio.run(main())
