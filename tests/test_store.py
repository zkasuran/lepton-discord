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
