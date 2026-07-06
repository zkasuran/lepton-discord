"""SQLite-backed async payment record store."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import aiosqlite

from ..domain.models import PaymentRecord, PaymentStatus

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS payment_records (
    payment_id       TEXT PRIMARY KEY,
    guild_id         TEXT NOT NULL,
    channel_id       TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    command_name     TEXT NOT NULL,
    command_args     TEXT NOT NULL DEFAULT '{}',
    price_atomic     INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',
    tx_hash          TEXT NOT NULL DEFAULT '',
    payer_address    TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    paid_at          TEXT,
    result           TEXT NOT NULL DEFAULT '',
    interaction_token TEXT NOT NULL DEFAULT '',
    application_id   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS guild_commands (
    guild_id        TEXT NOT NULL,
    command_name    TEXT NOT NULL,
    price_atomic    INTEGER NOT NULL DEFAULT 10000,
    description     TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, command_name)
);
"""


class PaymentStore:
    """Async SQLite store for payment records and guild configs."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_CREATE_SQL)
            await db.commit()

    async def create_payment(self, record: PaymentRecord) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                INSERT INTO payment_records (
                    payment_id, guild_id, channel_id, user_id,
                    command_name, command_args, price_atomic,
                    status, tx_hash, payer_address, created_at, paid_at,
                    result, interaction_token, application_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.payment_id,
                    record.guild_id,
                    record.channel_id,
                    record.user_id,
                    record.command_name,
                    json.dumps(record.command_args),
                    record.price_atomic,
                    record.status.value,
                    record.tx_hash,
                    record.payer_address,
                    record.created_at.isoformat(),
                    record.paid_at.isoformat() if record.paid_at else None,
                    record.result,
                    record.interaction_token,
                    record.application_id,
                ),
            )
            await db.commit()

    async def get_payment(self, payment_id: str) -> PaymentRecord | None:
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM payment_records WHERE payment_id = ?", (payment_id,)
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    async def mark_paid(
        self,
        payment_id: str,
        tx_hash: str,
        payer_address: str,
        result: str = "",
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                UPDATE payment_records
                SET status = ?, tx_hash = ?, payer_address = ?, paid_at = ?, result = ?
                WHERE payment_id = ?
                """,
                (
                    PaymentStatus.PAID.value,
                    tx_hash,
                    payer_address,
                    datetime.now(timezone.utc).isoformat(),
                    result,
                    payment_id,
                ),
            )
            await db.commit()

    async def recent_settlements(self, limit: int = 5) -> list[PaymentRecord]:
        """Return the most recent real settlements, newest first.

        A settlement is a PAID record with a non-empty on-chain tx hash, so a
        pending or failed attempt (or a paid row that never got a hash) never
        shows up on the public proof wall.
        """
        async with aiosqlite.connect(self._path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM payment_records WHERE status = ? AND tx_hash != ''"
                " ORDER BY paid_at DESC, created_at DESC LIMIT ?",
                (PaymentStatus.PAID.value, limit),
            ) as cursor:
                rows = await cursor.fetchall()
        return [_row_to_record(row) for row in rows]

    async def total_spent_atomic(self, user_id: str) -> int:
        """Sum of settled (paid) spend for a user, in USDC atomic units."""
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT COALESCE(SUM(price_atomic), 0) FROM payment_records"
                " WHERE user_id = ? AND status = ?",
                (user_id, PaymentStatus.PAID.value),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    async def mark_failed(self, payment_id: str, reason: str = "") -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "UPDATE payment_records SET status = ?, result = ? WHERE payment_id = ?",
                (PaymentStatus.FAILED.value, reason, payment_id),
            )
            await db.commit()

    async def set_command_price(
        self,
        guild_id: str,
        command_name: str,
        price_atomic: int,
        description: str = "",
        enabled: bool = True,
    ) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                """
                INSERT INTO guild_commands
                    (guild_id, command_name, price_atomic, description, enabled)
                VALUES (?,?,?,?,?)
                ON CONFLICT(guild_id, command_name)
                DO UPDATE SET price_atomic=excluded.price_atomic,
                              description=excluded.description,
                              enabled=excluded.enabled
                """,
                (guild_id, command_name, price_atomic, description, 1 if enabled else 0),
            )
            await db.commit()

    async def get_command_price(self, guild_id: str, command_name: str) -> int:
        """Return price in atomic units for the command. Returns 0 if not configured."""
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT price_atomic FROM guild_commands"
                " WHERE guild_id=? AND command_name=? AND enabled=1",
                (guild_id, command_name),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def list_guild_commands(self, guild_id: str) -> list[tuple[str, int, str]]:
        """Return list of (command_name, price_atomic, description)."""
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT command_name, price_atomic, description"
                " FROM guild_commands WHERE guild_id=? AND enabled=1",
                (guild_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [(r[0], r[1], r[2]) for r in rows]


def _row_to_record(row: sqlite3.Row) -> PaymentRecord:
    return PaymentRecord(
        payment_id=row["payment_id"],
        guild_id=row["guild_id"],
        channel_id=row["channel_id"],
        user_id=row["user_id"],
        command_name=row["command_name"],
        command_args=json.loads(row["command_args"]),
        price_atomic=row["price_atomic"],
        status=PaymentStatus(row["status"]),
        tx_hash=row["tx_hash"],
        payer_address=row["payer_address"],
        created_at=datetime.fromisoformat(row["created_at"]),
        paid_at=datetime.fromisoformat(row["paid_at"]) if row["paid_at"] else None,
        result=row["result"],
        interaction_token=row["interaction_token"],
        application_id=row["application_id"],
    )
