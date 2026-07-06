"""Tests for PaymentStore (SQLite)."""

from __future__ import annotations

import pytest

from src.domain.models import PaymentRecord, PaymentStatus
from src.payments.store import PaymentStore


@pytest.fixture
async def store(tmp_path) -> PaymentStore:  # type: ignore[type-arg]
    s = PaymentStore(str(tmp_path / "test.db"))
    await s.init()
    return s


async def test_create_and_get_payment(store: PaymentStore) -> None:
    record = PaymentRecord(
        guild_id="123",
        channel_id="456",
        user_id="789",
        command_name="weather",
        command_args={"city": "London"},
        price_atomic=10_000,
    )
    await store.create_payment(record)
    fetched = await store.get_payment(record.payment_id)
    assert fetched is not None
    assert fetched.payment_id == record.payment_id
    assert fetched.status == PaymentStatus.PENDING
    assert fetched.command_args == {"city": "London"}


async def test_mark_paid(store: PaymentStore) -> None:
    record = PaymentRecord(guild_id="g", channel_id="c", user_id="u", command_name="ping")
    await store.create_payment(record)
    await store.mark_paid(record.payment_id, "0xdeadbeef", "0xpayer", "Pong!")
    fetched = await store.get_payment(record.payment_id)
    assert fetched is not None
    assert fetched.status == PaymentStatus.PAID
    assert fetched.tx_hash == "0xdeadbeef"
    assert fetched.result == "Pong!"
    assert fetched.paid_at is not None


async def test_mark_failed(store: PaymentStore) -> None:
    record = PaymentRecord(guild_id="g", channel_id="c", user_id="u", command_name="ping")
    await store.create_payment(record)
    await store.mark_failed(record.payment_id, "insufficient balance")
    fetched = await store.get_payment(record.payment_id)
    assert fetched is not None
    assert fetched.status == PaymentStatus.FAILED


async def test_get_missing_payment(store: PaymentStore) -> None:
    result = await store.get_payment("nonexistent-id")
    assert result is None


async def test_set_and_get_command_price(store: PaymentStore) -> None:
    await store.set_command_price("guild1", "weather", 50_000, "Weather command")
    price = await store.get_command_price("guild1", "weather")
    assert price == 50_000


async def test_get_unconfigured_command_price(store: PaymentStore) -> None:
    price = await store.get_command_price("guild1", "nonexistent")
    assert price == 0


async def test_list_guild_commands(store: PaymentStore) -> None:
    await store.set_command_price("g", "weather", 10_000, "Weather")
    await store.set_command_price("g", "price", 20_000, "Price")
    cmds = await store.list_guild_commands("g")
    names = {c[0] for c in cmds}
    assert "weather" in names
    assert "price" in names


async def test_set_command_price_upsert(store: PaymentStore) -> None:
    await store.set_command_price("g", "weather", 10_000)
    await store.set_command_price("g", "weather", 99_000)
    price = await store.get_command_price("g", "weather")
    assert price == 99_000


async def test_recent_settlements_only_returns_settled(store: PaymentStore) -> None:
    # one settled, one still pending, one failed
    paid = PaymentRecord(guild_id="web", command_name="crypto_price", price_atomic=1_000)
    await store.create_payment(paid)
    await store.mark_paid(paid.payment_id, "0xabc", "0xpayer", "BTC $62k")

    pending = PaymentRecord(guild_id="g", command_name="weather")
    await store.create_payment(pending)

    failed = PaymentRecord(guild_id="g", command_name="ping")
    await store.create_payment(failed)
    await store.mark_failed(failed.payment_id, "no funds")

    rows = await store.recent_settlements()
    assert [r.payment_id for r in rows] == [paid.payment_id]
    assert all(r.status == PaymentStatus.PAID and r.tx_hash for r in rows)


async def test_recent_settlements_excludes_paid_without_hash(store: PaymentStore) -> None:
    # a PAID row with an empty tx_hash is not a real on-chain settlement
    rec = PaymentRecord(guild_id="g", command_name="ping")
    await store.create_payment(rec)
    await store.mark_paid(rec.payment_id, "", "0xpayer", "pong")
    assert await store.recent_settlements() == []


async def test_recent_settlements_honors_limit_and_orders_newest_first(
    store: PaymentStore,
) -> None:
    for i in range(5):
        rec = PaymentRecord(guild_id="web", command_name="crypto_price", price_atomic=1_000)
        await store.create_payment(rec)
        await store.mark_paid(rec.payment_id, f"0x{i}", "0xpayer", f"tx {i}")

    rows = await store.recent_settlements(limit=3)
    assert len(rows) == 3
    # DESC by paid_at: first row is settled no earlier than the last
    assert rows[0].paid_at is not None and rows[-1].paid_at is not None
    assert rows[0].paid_at >= rows[-1].paid_at
