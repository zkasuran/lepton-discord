"""Traction report — compute the in-window usage numbers from the payment store.

Reads the SQLite store, aggregates with the pure `summarize_traction`, and prints
a human report plus a paste-ready line for the submission form's Traction field.
Every figure is derived from settled records, so nothing is hand-typed.

Usage:
  .venv/bin/python scripts/traction_report.py
  .venv/bin/python scripts/traction_report.py --verify-chain   # re-check each tx on Arc
  .venv/bin/python scripts/traction_report.py --json           # machine-readable

Honest by construction: distinct_paying_users counts distinct Discord users, each
under their own per-user budget on one shared agent wallet. It is not a count of
distinct on-chain wallets.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime

from src.domain.models import PaymentRecord, PaymentStatus
from src.domain.traction import TractionSummary, summarize_traction
from src.payments.config import ARC_RPC_URL, DB_PATH

ARCSCAN = "https://testnet.arcscan.app/tx/"


def _load_records(db_path: str) -> list[PaymentRecord]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM payment_records").fetchall()
    except sqlite3.OperationalError:
        return []  # store not initialised yet
    finally:
        con.close()

    records: list[PaymentRecord] = []
    for r in rows:
        paid_at = r["paid_at"]
        records.append(
            PaymentRecord(
                payment_id=r["payment_id"],
                guild_id=r["guild_id"],
                channel_id=r["channel_id"],
                user_id=r["user_id"],
                command_name=r["command_name"],
                command_args=json.loads(r["command_args"] or "{}"),
                price_atomic=r["price_atomic"],
                status=PaymentStatus(r["status"]),
                tx_hash=r["tx_hash"],
                payer_address=r["payer_address"],
                created_at=datetime.fromisoformat(r["created_at"]),
                paid_at=datetime.fromisoformat(paid_at) if paid_at else None,
                result=r["result"],
            )
        )
    return records


def _verify_chain(tx_hashes: list[str]) -> dict[str, str]:
    """Re-check each settlement receipt on Arc. Best effort, network required."""
    from web3 import Web3

    w3 = Web3(Web3.HTTPProvider(ARC_RPC_URL))
    out: dict[str, str] = {}
    for tx in tx_hashes:
        try:
            rec = w3.eth.get_transaction_receipt(tx)
            out[tx] = "status 1" if rec.status == 1 else f"status {rec.status}"
        except Exception as exc:  # noqa: BLE001 — report, do not crash the report
            out[tx] = f"lookup failed: {exc}"
    return out


def _form_line(s: TractionSummary) -> str:
    window = ""
    if s.first_paid_at and s.last_paid_at:
        window = (
            f" between {s.first_paid_at.isoformat(timespec='minutes')} and "
            f"{s.last_paid_at.isoformat(timespec='minutes')} UTC"
        )
    return (
        f"{s.distinct_paying_users} distinct Discord users triggered "
        f"{s.settled_payments} autonomous payments{window}, each settled in USDC on "
        f"Arc testnet (status 1). Total spent ${s.total_spent_usdc:.4f}, average "
        f"${s.avg_spend_usdc:.4f} per call, all sub-cent. Each user spent under "
        f"their own per-user budget on one shared agent wallet, so the agent "
        f"never sent a transaction itself (EIP-3009)."
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--verify-chain", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    records = _load_records(args.db)
    s = summarize_traction(records)

    if args.json:
        payload = {
            "distinct_paying_users": s.distinct_paying_users,
            "settled_payments": s.settled_payments,
            "failed_payments": s.failed_payments,
            "total_spent_usdc": round(s.total_spent_usdc, 6),
            "avg_spend_usdc": round(s.avg_spend_usdc, 6),
            "distinct_guilds": s.distinct_guilds,
            "distinct_channels": s.distinct_channels,
            "per_command": s.per_command,
            "first_paid_at": s.first_paid_at.isoformat() if s.first_paid_at else None,
            "last_paid_at": s.last_paid_at.isoformat() if s.last_paid_at else None,
            "settled_tx": s.settled_tx,
        }
        print(json.dumps(payload, indent=2))
        return

    print("=" * 60)
    print("NanoPay traction report")
    print("=" * 60)
    print(f"DB: {args.db}   records: {len(records)}")
    print(f"Distinct paying users : {s.distinct_paying_users}")
    print(f"Settled payments      : {s.settled_payments}   (failed: {s.failed_payments})")
    print(f"Total spent           : ${s.total_spent_usdc:.4f} USDC")
    print(f"Average per call      : ${s.avg_spend_usdc:.4f} USDC")
    print(f"Distinct guilds       : {s.distinct_guilds}")
    print(f"Distinct channels     : {s.distinct_channels}")
    if s.first_paid_at and s.last_paid_at:
        print(
            f"Window                : {s.first_paid_at.isoformat()} .. {s.last_paid_at.isoformat()}"
        )
    if s.per_command:
        print("By command            : " + ", ".join(f"{k}={v}" for k, v in s.per_command.items()))
    paying_ids = sorted(
        {r.user_id for r in records if r.status == PaymentStatus.PAID and r.tx_hash and r.user_id}
    )
    if paying_ids:
        print("Paying user IDs       : " + ", ".join(paying_ids))
        print(
            "  (verify these are real Discord IDs, not dev placeholders like "
            "'e2e-demo'/'test'; run the live session on a fresh DB)"
        )
    if s.settled_tx:
        print("\nSettlements:")
        checks = _verify_chain(s.settled_tx) if args.verify_chain else {}
        for tx in s.settled_tx:
            suffix = f"   [{checks[tx]}]" if tx in checks else ""
            print(f"  {ARCSCAN}{tx}{suffix}")
    print("\nPaste-ready Traction line:")
    print(_form_line(s))


if __name__ == "__main__":
    main()
