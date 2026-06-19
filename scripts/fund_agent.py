"""Generate (if needed) and fund the agent wallet with testnet USDC on Arc.

On Arc, USDC is the native gas token, so a native value transfer credits the
ERC-20 USDC balance the EIP-3009 "exact" scheme needs. The agent only ever signs
authorizations (no gas), so it just needs USDC to cover the spend.

Usage:
  .venv/bin/python scripts/fund_agent.py [target_usdc]

Funds from DEPLOYER_PRIVATE_KEY. Writes a fresh AGENT_PRIVATE_KEY into .env if one
is not set. Idempotent: only tops up to the target.
"""

from __future__ import annotations

import sys
from pathlib import Path

from eth_account import Account
from web3 import Web3

from src.payments.config import ARC_CHAIN_ID, ARC_RPC_URL, DEPLOYER_PRIVATE_KEY

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
USDC_DECIMALS_NATIVE = 10**18  # native gas view (USDC is the gas token on Arc)


def _read_or_make_agent_key() -> str:
    import os

    key = os.getenv("AGENT_PRIVATE_KEY", "").strip()
    if key:
        return key
    acct = Account.create()
    key = acct.key.hex()
    if not key.startswith("0x"):
        key = "0x" + key
    with ENV_PATH.open("a") as fh:
        fh.write(f"\n# Generated agent wallet (payer)\nAGENT_PRIVATE_KEY={key}\n")
    print(f"Generated new agent wallet {acct.address} and wrote AGENT_PRIVATE_KEY to .env")
    return key


def main() -> None:
    target_usdc = float(sys.argv[1]) if len(sys.argv) > 1 else 0.05
    if not DEPLOYER_PRIVATE_KEY:
        raise SystemExit("DEPLOYER_PRIVATE_KEY not set; cannot fund.")

    w3 = Web3(Web3.HTTPProvider(ARC_RPC_URL))
    funder = Account.from_key(DEPLOYER_PRIVATE_KEY)
    agent_key = _read_or_make_agent_key()
    agent = Account.from_key(agent_key)

    target_wei = int(target_usdc * USDC_DECIMALS_NATIVE)
    bal = w3.eth.get_balance(agent.address)
    print(f"Funder: {funder.address}")
    print(f"Agent:  {agent.address}")
    print(f"Agent balance: {bal / USDC_DECIMALS_NATIVE:.6f} USDC (target {target_usdc})")
    if bal >= target_wei:
        print("Agent already funded to target. Nothing to do.")
        return

    need = target_wei - bal
    tx = {
        "to": agent.address,
        "value": need,
        "nonce": w3.eth.get_transaction_count(funder.address),
        "gas": 21000,
        "gasPrice": w3.eth.gas_price,
        "chainId": ARC_CHAIN_ID,
    }
    signed = funder.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"Funding tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"Confirmed in block {receipt.blockNumber}, status {receipt.status}")
    bal2 = w3.eth.get_balance(agent.address)
    print(f"Agent balance now: {bal2 / USDC_DECIMALS_NATIVE:.6f} USDC")


if __name__ == "__main__":
    main()
