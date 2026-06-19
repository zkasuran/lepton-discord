"""Tests for domain models."""

from __future__ import annotations

from src.domain.models import (
    CommandConfig,
    GuildConfig,
    PaymentRecord,
    PaymentStatus,
)


def test_payment_record_defaults() -> None:
    r = PaymentRecord()
    assert r.status == PaymentStatus.PENDING
    assert r.payment_id != ""
    assert r.tx_hash == ""


def test_command_config_price_display() -> None:
    cfg = CommandConfig(guild_id="g1", command_name="weather", price_atomic=10_000)
    assert cfg.price_display == "$0.0100"


def test_command_config_price_display_cents() -> None:
    cfg = CommandConfig(guild_id="g1", command_name="gpt", price_atomic=100_000)
    assert cfg.price_display == "$0.1000"


def test_guild_config_empty_commands() -> None:
    gc = GuildConfig(guild_id="g1", owner_id="u1")
    assert gc.commands == {}
    assert gc.enabled is True


def test_payment_record_unique_ids() -> None:
    r1 = PaymentRecord()
    r2 = PaymentRecord()
    assert r1.payment_id != r2.payment_id
