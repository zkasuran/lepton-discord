"""Tests for the traction summary aggregation."""

from __future__ import annotations

from datetime import datetime, timezone

from src.domain.models import PaymentRecord, PaymentStatus
from src.domain.traction import summarize_traction


def test_mixed_naive_and_aware_paid_times_do_not_crash() -> None:
    """Older store rows can be tz-naive while newer ones are tz-aware; sorting
    the window must tolerate both instead of raising a TypeError."""
    naive = PaymentRecord(
        user_id="alice",
        command_name="ask",
        price_atomic=10_000,
        status=PaymentStatus.PAID,
        tx_hash="0xaa",
        paid_at=datetime(2026, 7, 4, 10, 0, 0),  # naive
    )
    aware = PaymentRecord(
        user_id="bob",
        command_name="ask",
        price_atomic=10_000,
        status=PaymentStatus.PAID,
        tx_hash="0xbb",
        paid_at=datetime(2026, 7, 4, 11, 0, 0, tzinfo=timezone.utc),  # aware
    )
    s = summarize_traction([naive, aware])
    assert s.settled_payments == 2
    assert s.first_paid_at is not None and s.last_paid_at is not None
    assert s.first_paid_at.hour == 10
    assert s.last_paid_at.hour == 11


def _paid(
    user_id: str,
    command_name: str,
    price_atomic: int,
    tx_hash: str,
    minute: int,
    guild_id: str = "g1",
    channel_id: str = "c1",
) -> PaymentRecord:
    return PaymentRecord(
        guild_id=guild_id,
        channel_id=channel_id,
        user_id=user_id,
        command_name=command_name,
        price_atomic=price_atomic,
        status=PaymentStatus.PAID,
        tx_hash=tx_hash,
        paid_at=datetime(2026, 7, 5, 12, minute, 0, tzinfo=timezone.utc),
    )


def test_empty_records_zeroed() -> None:
    s = summarize_traction([])
    assert s.distinct_paying_users == 0
    assert s.settled_payments == 0
    assert s.total_spent_atomic == 0
    assert s.avg_spend_atomic == 0
    assert s.avg_spend_usdc == 0.0
    assert s.first_paid_at is None
    assert s.last_paid_at is None


def test_counts_distinct_users_and_totals() -> None:
    records = [
        _paid("alice", "price", 1_000, "0xaa", minute=1),
        _paid("alice", "ask", 10_000, "0xbb", minute=3),
        _paid("bob", "weather", 1_000, "0xcc", minute=2),
    ]
    s = summarize_traction(records)
    assert s.distinct_paying_users == 2
    assert s.settled_payments == 3
    assert s.total_spent_atomic == 12_000
    assert s.total_spent_usdc == 0.012
    assert s.avg_spend_atomic == 4_000
    assert s.per_command == {"price": 1, "ask": 1, "weather": 1}
    assert s.settled_tx == ["0xaa", "0xbb", "0xcc"]


def test_window_uses_first_and_last_paid_time() -> None:
    records = [
        _paid("alice", "ask", 10_000, "0xbb", minute=9),
        _paid("bob", "ask", 10_000, "0xcc", minute=2),
    ]
    s = summarize_traction(records)
    assert s.first_paid_at is not None and s.first_paid_at.minute == 2
    assert s.last_paid_at is not None and s.last_paid_at.minute == 9


def test_pending_and_failed_never_inflate_settlements() -> None:
    pending = PaymentRecord(user_id="carol", command_name="ask", price_atomic=10_000)
    failed = PaymentRecord(
        user_id="dave",
        command_name="ask",
        price_atomic=10_000,
        status=PaymentStatus.FAILED,
    )
    paid_no_hash = PaymentRecord(
        user_id="erin",
        command_name="ask",
        price_atomic=10_000,
        status=PaymentStatus.PAID,
        tx_hash="",
    )
    records = [pending, failed, paid_no_hash, _paid("alice", "price", 1_000, "0xaa", 1)]
    s = summarize_traction(records)
    assert s.settled_payments == 1
    assert s.failed_payments == 1
    assert s.distinct_paying_users == 1
    assert s.total_spent_atomic == 1_000


def test_distinct_guilds_and_channels() -> None:
    records = [
        _paid("alice", "ask", 10_000, "0xaa", 1, guild_id="g1", channel_id="c1"),
        _paid("bob", "ask", 10_000, "0xbb", 2, guild_id="g2", channel_id="c2"),
        _paid("carol", "ask", 10_000, "0xcc", 3, guild_id="g2", channel_id="c3"),
    ]
    s = summarize_traction(records)
    assert s.distinct_guilds == 2
    assert s.distinct_channels == 3
