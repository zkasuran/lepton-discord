"""Traction summary — pure aggregation over payment records, no IO.

The submission is judged partly on traction: genuine in-window usage with real
USDC settling on Arc. These are RFB-1's own metrics (distinct payers, settlement
count, average spend). This module turns the raw payment records into those
numbers so the figures we report are computed from the store, never hand-typed.

Honest framing baked into the field names: `distinct_paying_users` counts distinct
Discord users, each governed by their own per-user budget while sharing one agent
wallet. It is not a count of distinct on-chain wallets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import PaymentRecord, PaymentStatus


def _as_aware(dt: datetime) -> datetime:
    """Coerce a naive timestamp to UTC so mixed store rows sort together.

    Older store rows can be tz-naive while newer ones carry UTC. Treating a naive
    value as UTC keeps the window computation from raising on the comparison.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class TractionSummary:
    """Aggregated, verifiable usage figures for a set of payment records."""

    distinct_paying_users: int = 0
    settled_payments: int = 0
    failed_payments: int = 0
    total_spent_atomic: int = 0
    distinct_guilds: int = 0
    distinct_channels: int = 0
    per_command: dict[str, int] = field(default_factory=dict)
    first_paid_at: datetime | None = None
    last_paid_at: datetime | None = None
    settled_tx: list[str] = field(default_factory=list)

    @property
    def total_spent_usdc(self) -> float:
        return self.total_spent_atomic / 1_000_000

    @property
    def avg_spend_atomic(self) -> int:
        if self.settled_payments == 0:
            return 0
        return self.total_spent_atomic // self.settled_payments

    @property
    def avg_spend_usdc(self) -> float:
        if self.settled_payments == 0:
            return 0.0
        return (self.total_spent_atomic / self.settled_payments) / 1_000_000


def summarize_traction(records: list[PaymentRecord]) -> TractionSummary:
    """Reduce payment records to the traction figures, counting only real settlements.

    A settled payment is one marked PAID with a non-empty on-chain tx hash, so a
    pending or failed attempt never inflates the numbers.
    """
    settled = [r for r in records if r.status == PaymentStatus.PAID and r.tx_hash]
    failed = [r for r in records if r.status == PaymentStatus.FAILED]

    per_command: dict[str, int] = {}
    for r in settled:
        per_command[r.command_name] = per_command.get(r.command_name, 0) + 1

    paid_times = sorted(_as_aware(r.paid_at) for r in settled if r.paid_at is not None)

    return TractionSummary(
        distinct_paying_users=len({r.user_id for r in settled if r.user_id}),
        settled_payments=len(settled),
        failed_payments=len(failed),
        total_spent_atomic=sum(r.price_atomic for r in settled),
        distinct_guilds=len({r.guild_id for r in settled if r.guild_id}),
        distinct_channels=len({r.channel_id for r in settled if r.channel_id}),
        per_command=per_command,
        first_paid_at=paid_times[0] if paid_times else None,
        last_paid_at=paid_times[-1] if paid_times else None,
        settled_tx=[r.tx_hash for r in settled],
    )
