"""Domain models for NanoPay for Discord."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PaymentStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    FAILED = "failed"


class CommandStatus(str, Enum):
    QUEUED = "queued"
    AWAITING_PAYMENT = "awaiting_payment"
    EXECUTING = "executing"
    DONE = "done"
    FAILED = "failed"


@dataclass
class CommandConfig:
    """A premium command registered by a server owner."""

    guild_id: str
    command_name: str
    # price in USDC atomic units (6 decimals) — e.g. 10000 = $0.01
    price_atomic: int
    description: str = ""
    enabled: bool = True

    @property
    def price_display(self) -> str:
        dollars = self.price_atomic / 1_000_000
        return f"${dollars:.4f}"


@dataclass
class PaymentRecord:
    """One x402 payment attempt tied to a Discord interaction."""

    payment_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    guild_id: str = ""
    channel_id: str = ""
    user_id: str = ""
    command_name: str = ""
    command_args: dict[str, str] = field(default_factory=dict)
    price_atomic: int = 0
    status: PaymentStatus = PaymentStatus.PENDING
    tx_hash: str = ""
    payer_address: str = ""
    created_at: datetime = field(default_factory=_utcnow)
    paid_at: datetime | None = None
    # Result from the command after payment, stored for follow-up
    result: str = ""
    # Discord interaction token (valid 15 min) for follow-up
    interaction_token: str = ""
    # Discord application id for webhook follow-up
    application_id: str = ""


@dataclass
class GuildConfig:
    """Per-guild bot configuration."""

    guild_id: str
    owner_id: str
    # Per-command configs keyed by command_name
    commands: dict[str, CommandConfig] = field(default_factory=dict)
    enabled: bool = True
