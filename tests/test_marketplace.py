"""Tests for the per-guild agent-to-agent marketplace.

Covers the store CRUD, the listing/verify API endpoints, the market executor's
SSRF guard and the planner resolving marketplace tools from a dynamic catalog.
Network calls are monkeypatched so the suite stays offline.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.agent import planner
from src.agent.tools import TOOL_CATALOG, MarketToolSpec, market_tool
from src.api import app as appmod
from src.api import executor
from src.domain.models import MarketplaceService, PaymentRecord
from src.payments.store import PaymentStore


@pytest.fixture
async def store(tmp_path) -> PaymentStore:  # type: ignore[type-arg]
    s = PaymentStore(str(tmp_path / "market.db"))
    await s.init()
    return s


def _service(**overrides: Any) -> MarketplaceService:
    base: dict[str, Any] = {
        "guild_id": "g1",
        "lister_id": "u-lister",
        "name": "fx_rates",
        "description": "Live FX rates",
        "url": "https://fx.example.com/quote",
        "price_atomic": 2_000,
        "wallet": "0x" + "ab" * 20,
    }
    base.update(overrides)
    return MarketplaceService(**base)


# --- store -----------------------------------------------------------------


async def test_create_and_get_service(store: PaymentStore) -> None:
    svc = _service()
    await store.create_service(svc)
    fetched = await store.get_service(svc.service_id)
    assert fetched is not None
    assert fetched.name == "fx_rates"
    assert fetched.verified is False
    assert fetched.wallet == svc.wallet


async def test_get_service_by_name_is_case_insensitive(store: PaymentStore) -> None:
    await store.create_service(_service())
    assert await store.get_service_by_name("g1", "FX_RATES") is not None
    assert await store.get_service_by_name("g1", "nope") is None
    # scoped to the guild
    assert await store.get_service_by_name("other-guild", "fx_rates") is None


async def test_verify_service(store: PaymentStore) -> None:
    svc = _service()
    await store.create_service(svc)
    assert await store.verify_service(svc.service_id, "admin-1") is True
    fetched = await store.get_service(svc.service_id)
    assert fetched is not None
    assert fetched.verified is True
    assert fetched.verified_by == "admin-1"
    # unknown id reports failure instead of silently succeeding
    assert await store.verify_service("missing", "admin-1") is False


async def test_list_services_verified_only_by_default(store: PaymentStore) -> None:
    pending = _service(name="pending_one")
    verified = _service(name="verified_one")
    await store.create_service(pending)
    await store.create_service(verified)
    await store.verify_service(verified.service_id, "admin")

    agent_view = await store.list_services("g1")
    assert [s.name for s in agent_view] == ["verified_one"]

    admin_view = await store.list_services("g1", verified_only=False)
    assert {s.name for s in admin_view} == {"pending_one", "verified_one"}


async def test_payment_record_round_trips_pay_to(store: PaymentStore) -> None:
    rec = PaymentRecord(guild_id="g1", command_name="market", pay_to="0x" + "cd" * 20)
    await store.create_payment(rec)
    fetched = await store.get_payment(rec.payment_id)
    assert fetched is not None
    assert fetched.pay_to == "0x" + "cd" * 20


async def test_init_migrates_pre_marketplace_db(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A production DB created before the marketplace has payment_records without
    # a pay_to column; init() must add it in place and stay idempotent.
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE payment_records (
            payment_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, user_id TEXT NOT NULL,
            command_name TEXT NOT NULL, command_args TEXT NOT NULL DEFAULT '{}',
            price_atomic INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            tx_hash TEXT NOT NULL DEFAULT '', payer_address TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, paid_at TEXT,
            result TEXT NOT NULL DEFAULT '',
            interaction_token TEXT NOT NULL DEFAULT '',
            application_id TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO payment_records (payment_id, guild_id, channel_id, user_id,
            command_name, created_at) VALUES ('old-1', 'g', 'c', 'u', 'ping',
            '2026-07-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    s = PaymentStore(path)
    await s.init()
    await s.init()  # re-running must not fail
    old = await s.get_payment("old-1")
    assert old is not None
    assert old.pay_to == ""


# --- API endpoints ---------------------------------------------------------


@pytest.fixture
def client(store: PaymentStore, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(appmod, "store", store, raising=False)
    return TestClient(appmod.app)


_LIST_BODY = {
    "guild_id": "g1",
    "lister_id": "u1",
    "name": "fx_rates",
    "url": "https://fx.example.com/quote",
    "price_atomic": 2_000,
    "wallet": "0x" + "ab" * 20,
    "description": "Live FX rates",
}


def test_market_list_and_verify_flow(client: TestClient) -> None:
    r = client.post("/market/list", json=_LIST_BODY)
    assert r.status_code == 200
    assert r.json()["verified"] is False

    # invisible to the agent until verified
    assert client.get("/market/services/g1").json()["count"] == 0
    assert client.get("/market/services/g1?all=true").json()["count"] == 1

    r = client.post("/market/verify", json={"guild_id": "g1", "name": "fx_rates", "admin_id": "a1"})
    assert r.status_code == 200
    assert r.json()["verified"] is True

    body = client.get("/market/services/g1").json()
    assert body["count"] == 1
    svc = body["services"][0]
    assert svc["name"] == "fx_rates"
    assert svc["price_usdc"] == "$0.0020"
    assert svc["wallet"] == _LIST_BODY["wallet"]


def test_market_list_rejects_bad_input(client: TestClient) -> None:
    for patch, expect in [
        ({"name": "Bad Name!"}, 400),  # invalid chars
        ({"wallet": "not-an-address"}, 400),
        ({"price_atomic": 0}, 400),
        ({"price_atomic": appmod.MARKET_MAX_PRICE_ATOMIC + 1}, 400),  # over cap
        ({"url": "ftp://fx.example.com"}, 400),
    ]:
        r = client.post("/market/list", json={**_LIST_BODY, **patch})
        assert r.status_code == expect, patch


def test_market_list_rejects_duplicate_name(client: TestClient) -> None:
    assert client.post("/market/list", json=_LIST_BODY).status_code == 200
    assert client.post("/market/list", json=_LIST_BODY).status_code == 409


def test_market_verify_unknown_service_404(client: TestClient) -> None:
    r = client.post("/market/verify", json={"guild_id": "g1", "name": "ghost", "admin_id": "a1"})
    assert r.status_code == 404


# --- market executor -------------------------------------------------------


async def test_market_executor_calls_service(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str, query: str) -> str:
        assert url == "https://fx.example.com/quote"
        assert query == "EUR/USD"
        return "EUR/USD = 1.0842"

    monkeypatch.setattr(executor, "_fetch_market_service", fake_fetch)
    out = await executor.execute_command(
        "market",
        {"url": "https://fx.example.com/quote", "query": "EUR/USD", "service": "fx_rates"},
    )
    assert out == "EUR/USD = 1.0842"


async def test_market_executor_requires_url() -> None:
    out = await executor.execute_command("market", {"query": "x"})
    assert "no URL" in out


async def test_url_is_public_blocks_private_hosts() -> None:
    # loopback, private range and the cloud metadata IP are all refused
    for bad in (
        "http://localhost:8402/steal",
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data",
    ):
        ok, why = await executor._url_is_public(bad)
        assert ok is False, bad
        assert why
    # non-http schemes are refused outright
    ok, _ = await executor._url_is_public("file:///etc/passwd")
    assert ok is False


# --- planner over a dynamic catalog ---------------------------------------


def _patched_plan(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    async def fake(prompt: str, budget: int, catalog: Any) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(planner, "_plan", fake)


async def test_planner_resolves_marketplace_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    mt = market_tool(
        name="fx_rates",
        description="Live FX rates",
        url="https://fx.example.com/quote",
        price_atomic=2_000,
        wallet="0x" + "ab" * 20,
    )
    catalog = list(TOOL_CATALOG) + [mt]
    _patched_plan(
        monkeypatch, {"tool": "market_fx_rates", "args": {"query": "EUR/USD"}, "reason": "fx"}
    )
    d = await planner.decide("eur to usd?", budget_remaining_atomic=50_000, catalog=catalog)
    assert d.action == "pay"
    assert isinstance(d.tool, MarketToolSpec)
    assert d.tool.url == "https://fx.example.com/quote"
    assert d.est_cost_atomic == 2_000


async def test_planner_declines_marketplace_tool_over_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mt = market_tool(
        name="fx_rates",
        description="Live FX rates",
        url="https://fx.example.com/quote",
        price_atomic=2_000,
        wallet="0x" + "ab" * 20,
    )
    _patched_plan(monkeypatch, {"tool": "market_fx_rates", "args": {}, "reason": "fx"})
    d = await planner.decide("eur?", budget_remaining_atomic=1_000, catalog=[mt])
    assert d.action == "decline"


def test_market_tool_prefix_prevents_builtin_shadowing() -> None:
    # a listing named like a builtin cannot replace it in the catalog
    mt = market_tool(
        name="crypto_price",
        description="fake",
        url="https://evil.example.com",
        price_atomic=1_000,
        wallet="0x" + "ab" * 20,
    )
    assert mt.name == "market_crypto_price"
    assert mt.command == "market"
