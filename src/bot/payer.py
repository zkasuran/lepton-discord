"""Autonomous x402 payer for the Discord bot.

The agent signs EIP-3009 payments from its own wallet and retries requests
automatically. No MetaMask, no user action needed beyond the slash command.
With EIP-3009 the agent needs USDC only — the facilitator submits the on-chain
transfer and pays the gas.

Flow:
  POST /execute/{id}  -> 402 + requirements
  transport intercepts -> EthAccountSigner signs EIP-3009 (from = agent wallet)
  retry POST /execute/{id} with X-PAYMENT header
  -> 200 + result

This is the agentic payment demo: the bot is the x402 client paying a service.
"""

from __future__ import annotations

import logging

import httpx
from eth_account import Account
from x402 import x402Client
from x402.http.clients.httpx import x402HttpxClient
from x402.mechanisms.evm.exact.register import register_exact_evm_client
from x402.mechanisms.evm.signers import EthAccountSigner

from ..payments.config import AGENT_PRIVATE_KEY, API_BASE_URL, ARC_NETWORK

logger = logging.getLogger("nanopay.payer")


def build_paying_client() -> httpx.AsyncClient:
    """Return an httpx.AsyncClient that auto-pays 402 responses from the agent wallet."""
    if not AGENT_PRIVATE_KEY:
        raise RuntimeError("AGENT_PRIVATE_KEY not set (no agent wallet to pay from)")

    account = Account.from_key(AGENT_PRIVATE_KEY)
    signer = EthAccountSigner(account)

    x402 = x402Client()
    register_exact_evm_client(x402, signer, networks=ARC_NETWORK)

    logger.info("x402 agent payer ready — wallet %s on %s", account.address[:10], ARC_NETWORK)

    return x402HttpxClient(x402, base_url=API_BASE_URL, timeout=60)


async def pay_and_execute(
    client: httpx.AsyncClient,
    payment_id: str,
) -> dict[str, str]:
    """POST /execute/{payment_id} — transport auto-handles 402 and retries with payment.

    Returns the JSON response dict on success.
    Raises httpx.HTTPStatusError on non-402/non-200 responses.
    """
    resp = await client.post(f"/execute/{payment_id}")
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]
