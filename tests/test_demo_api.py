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
