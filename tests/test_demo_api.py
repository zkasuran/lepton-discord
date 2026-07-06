"""Tests for the public /demo/ask endpoint: rate limiting and decision routing.

The payment path is exercised live end to end elsewhere; here the planner is
mocked so the routing and the throttle are checked deterministically offline.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.agent import planner
from src.agent.planner import Decision
from src.api import app as appmod
from src.domain.models import PaymentRecord
from src.payments.store import PaymentStore


def test_rate_limit_blocks_after_max_then_recovers() -> None:
    ip = "203.0.113.9"
    appmod._demo_hits.clear()
    now = 1000.0
    for _ in range(appmod.DEMO_MAX_PER_MIN):
        assert appmod._demo_rate_ok(ip, now) is True
    # one past the limit within the same minute is blocked
    assert appmod._demo_rate_ok(ip, now) is False
    # once the rolling minute passes, it recovers
    assert appmod._demo_rate_ok(ip, now + 61) is True


def test_demo_ask_free_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_decide(prompt: str, budget: int, catalog: object = None) -> Decision:
        return Decision("answer_free", "no tool needed")

    async def fake_free(prompt: str) -> str:
        return "Hello, I am the NanoPay agent."

    monkeypatch.setattr(planner, "decide", fake_decide)
    monkeypatch.setattr(planner, "answer_free", fake_free)
    appmod._demo_hits.clear()

    client = TestClient(appmod.app)
    r = client.post("/demo/ask", json={"prompt": "hi"})
    assert r.status_code == 200
    d = r.json()
    assert d["action"] == "free"
    assert d["answer"] == "Hello, I am the NanoPay agent."
    assert d["spent_atomic"] == 0


def test_demo_ask_decline_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_decide(prompt: str, budget: int, catalog: object = None) -> Decision:
        return Decision("decline", "over the budget left")

    monkeypatch.setattr(planner, "decide", fake_decide)
    appmod._demo_hits.clear()

    client = TestClient(appmod.app)
    r = client.post("/demo/ask", json={"prompt": "buy something expensive"})
    assert r.status_code == 200
    d = r.json()
    assert d["action"] == "decline"
    assert d["spent_atomic"] == 0


def test_demo_ask_empty_prompt_is_free_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    appmod._demo_hits.clear()
    client = TestClient(appmod.app)
    r = client.post("/demo/ask", json={"prompt": "   "})
    assert r.status_code == 200
    assert r.json()["action"] == "free"


async def test_settlements_endpoint_returns_recent(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    store = PaymentStore(str(tmp_path / "s.db"))
    await store.init()

    rec = PaymentRecord(guild_id="web", command_name="crypto_price", price_atomic=1_000)
    await store.create_payment(rec)
    await store.mark_paid(rec.payment_id, "0xdeadbeef", "0xpayer", "BTC $62,486")

    # a pending record must not leak onto the proof wall
    await store.create_payment(PaymentRecord(guild_id="g", command_name="weather"))

    monkeypatch.setattr(appmod, "store", store, raising=False)
    client = TestClient(appmod.app)
    r = client.get("/settlements?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    row = body["settlements"][0]
    assert row["tx_hash"] == "0xdeadbeef"
    assert row["command"] == "crypto_price"
    assert row["amount_usdc"] == "0.0010"
    assert row["source"] == "web"


async def test_settlements_endpoint_normalizes_bare_hash(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    # older demo rows stored a bare hash; the explorer link needs a 0x prefix
    store = PaymentStore(str(tmp_path / "s.db"))
    await store.init()
    rec = PaymentRecord(guild_id="web", command_name="price", price_atomic=1_000)
    await store.create_payment(rec)
    await store.mark_paid(rec.payment_id, "5db3123102f1", "0xpayer", "BTC $62k")

    monkeypatch.setattr(appmod, "store", store, raising=False)
    client = TestClient(appmod.app)
    body = client.get("/settlements").json()
    assert body["settlements"][0]["tx_hash"] == "0x5db3123102f1"


async def test_settlements_endpoint_clamps_limit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    store = PaymentStore(str(tmp_path / "s.db"))
    await store.init()
    monkeypatch.setattr(appmod, "store", store, raising=False)
    client = TestClient(appmod.app)
    # oversized and undersized limits are clamped, never rejected
    assert client.get("/settlements?limit=9999").status_code == 200
    assert client.get("/settlements?limit=0").status_code == 200
