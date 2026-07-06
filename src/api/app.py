"""FastAPI application: x402 payment endpoints + command execution backend.

Routes:
  GET  /pay/{payment_id}      — HTML payment page (MetaMask or wallet link)
  POST /execute/{payment_id}  — Execute command after payment header verified
  GET  /status/{payment_id}   — Payment status (polled by bot)
  GET  /supported             — x402 facilitator supported schemes
  GET  /health                — Health check
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from x402.http.constants import PAYMENT_REQUIRED_HEADER, PAYMENT_SIGNATURE_HEADER
from x402.http.utils import (
    decode_payment_signature_header,
    encode_payment_required_header,
)
from x402.schemas import PaymentRequired, PaymentRequirements

from ..domain.models import PaymentRecord, PaymentStatus
from ..payments.config import (
    API_BASE_URL,
    ARC_NETWORK,
    ARC_USDC_ADDRESS,
    ARC_USDC_NAME,
    ARC_USDC_VERSION,
    DB_PATH,
    DEFAULT_BUDGET_ATOMIC,
    DEFAULT_PRICE_ATOMIC,
    SELLER_WALLET_ADDRESS,
)
from ..payments.facilitator import EmbeddedFacilitatorClient, build_facilitator
from ..payments.store import PaymentStore
from .executor import execute_command
from .paywall import build_payment_page

logger = logging.getLogger("nanopay.api")


# ============================================================================
# App state (populated in lifespan)
# ============================================================================

store: PaymentStore
facilitator_client: EmbeddedFacilitatorClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global store, facilitator_client
    store = PaymentStore(DB_PATH)
    await store.init()

    fac = build_facilitator()
    facilitator_client = EmbeddedFacilitatorClient(fac)

    logger.info("NanoPay API ready — Arc %s", ARC_NETWORK)
    yield
    logger.info("NanoPay API shutting down")


app = FastAPI(title="NanoPay for Discord", version="0.1.0", lifespan=lifespan)

# The public landing page (GitHub Pages) calls /demo/ask from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- public demo throttle: protect the agent wallet from a runaway browser ----
DEMO_REQUEST_BUDGET_ATOMIC = 20_000  # $0.02 budget the agent sees per demo call
DEMO_TOTAL_CAP_ATOMIC = 300_000  # $0.30 total the demo will ever spend, then free-only
DEMO_MAX_PER_MIN = 5  # per-IP requests per rolling minute
_demo_hits: dict[str, list[float]] = {}
_demo_spent = {"atomic": 0}


def _demo_rate_ok(ip: str, now: float) -> bool:
    """True if this IP is under the per-minute limit; records the hit if so."""
    hits = [t for t in _demo_hits.get(ip, []) if now - t < 60.0]
    if len(hits) >= DEMO_MAX_PER_MIN:
        _demo_hits[ip] = hits
        return False
    hits.append(now)
    _demo_hits[ip] = hits
    return True


# ============================================================================
# Helpers
# ============================================================================


def _build_requirements(price_atomic: int) -> PaymentRequirements:
    return PaymentRequirements.model_validate(
        {
            "scheme": "exact",
            "network": ARC_NETWORK,
            "asset": ARC_USDC_ADDRESS,
            "amount": str(price_atomic),
            "payTo": SELLER_WALLET_ADDRESS,
            "maxTimeoutSeconds": 60,
            "extra": {
                "name": ARC_USDC_NAME,
                "version": ARC_USDC_VERSION,
            },
        }
    )


def _402_response(reqs: PaymentRequirements) -> JSONResponse:
    """Return a proper x402 v2 response: requirements in PAYMENT-REQUIRED header."""
    pr = PaymentRequired(accepts=[reqs])
    encoded = encode_payment_required_header(pr)
    return JSONResponse(
        status_code=402,
        content={"x402Version": 2, "error": "payment required"},
        headers={PAYMENT_REQUIRED_HEADER: encoded},
    )


# ============================================================================
# Routes
# ============================================================================


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "network": ARC_NETWORK}


@app.get("/pay/{payment_id}", response_class=HTMLResponse)
async def payment_page(payment_id: str) -> HTMLResponse:
    record = await store.get_payment(payment_id)
    if record is None:
        raise HTTPException(404, "Payment not found")
    if record.status != PaymentStatus.PENDING:
        return HTMLResponse(
            f"<html><body style='font-family:system-ui;background:#0f0f0f;color:#4ade80;"
            f"display:flex;align-items:center;justify-content:center;height:100vh'>"
            f"<h2>Payment {record.status.value}. Check Discord for your result.</h2></body></html>"
        )

    price_atomic = record.price_atomic or DEFAULT_PRICE_ATOMIC
    reqs_dict = {
        "scheme": "exact",
        "network": ARC_NETWORK,
        "maxAmountRequired": str(price_atomic),
        "resource": f"{API_BASE_URL}/execute",
        "description": "NanoPay: premium command",
        "mimeType": "application/json",
        "payTo": SELLER_WALLET_ADDRESS,
        "maxTimeoutSeconds": 60,
        "asset": ARC_USDC_ADDRESS,
        "extra": {"name": ARC_USDC_NAME, "version": ARC_USDC_VERSION},
    }
    html = build_payment_page(record, reqs_dict, API_BASE_URL)
    return HTMLResponse(html)


@app.post("/execute/{payment_id}")
async def execute_paid_command(payment_id: str, request: Request) -> Response:
    """x402 v2 gated endpoint: verify PAYMENT-SIGNATURE header, settle, run command."""
    record = await store.get_payment(payment_id)
    if record is None:
        raise HTTPException(404, "Payment not found")

    if record.status == PaymentStatus.PAID:
        return JSONResponse({"ok": True, "result": record.result, "already_paid": True})

    # Look for v2 header first, then v1 fallback
    sig_header = (
        request.headers.get(PAYMENT_SIGNATURE_HEADER)
        or request.headers.get(PAYMENT_SIGNATURE_HEADER.lower())
        or request.headers.get("X-PAYMENT")
        or request.headers.get("x-payment")
    )

    price_atomic = record.price_atomic or DEFAULT_PRICE_ATOMIC
    reqs = _build_requirements(price_atomic)

    if not sig_header:
        return _402_response(reqs)

    # Parse payment payload (handles both v1 and v2)
    try:
        payload = decode_payment_signature_header(sig_header)
    except Exception as exc:
        logger.warning("Failed to parse payment header: %s", exc)
        raise HTTPException(400, f"Invalid payment header: {exc}") from exc

    verify_result = await facilitator_client.verify(payload, reqs)
    if not verify_result.is_valid:
        raise HTTPException(402, f"Payment verification failed: {verify_result.invalid_reason}")

    settle_result = await facilitator_client.settle(payload, reqs)
    if not settle_result.success:
        raise HTTPException(402, f"Payment settlement failed: {settle_result.error_reason}")

    tx_hash = settle_result.transaction or ""
    # Normalize to a 0x-prefixed hash so every downstream link (bot receipt, the
    # /demo response, the traction report) resolves on the block explorer, which
    # rejects a bare hash.
    if tx_hash and not tx_hash.startswith("0x"):
        tx_hash = "0x" + tx_hash
    payer = settle_result.payer or ""

    try:
        result_text = await execute_command(record.command_name, record.command_args)
    except Exception as exc:
        result_text = f"[Command error: {exc}]"

    await store.mark_paid(payment_id, tx_hash, payer, result_text)

    return JSONResponse(
        {
            "ok": True,
            "result": result_text,
            "tx_hash": tx_hash,
            "payer": payer,
        }
    )


@app.get("/status/{payment_id}")
async def payment_status(payment_id: str) -> dict[str, Any]:
    record = await store.get_payment(payment_id)
    if record is None:
        raise HTTPException(404, "Payment not found")
    return {
        "payment_id": payment_id,
        "status": record.status.value,
        "tx_hash": record.tx_hash,
        "result": record.result,
        "payer": record.payer_address,
    }


@app.post("/payments/create")
async def create_payment_record(body: dict[str, Any]) -> dict[str, str]:
    record = PaymentRecord(
        guild_id=body.get("guild_id", ""),
        channel_id=body.get("channel_id", ""),
        user_id=body.get("user_id", ""),
        command_name=body.get("command_name", ""),
        command_args=body.get("command_args", {}),
        price_atomic=int(body.get("price_atomic", DEFAULT_PRICE_ATOMIC)),
        interaction_token=body.get("interaction_token", ""),
        application_id=body.get("application_id", ""),
    )
    await store.create_payment(record)
    return {
        "payment_id": record.payment_id,
        "pay_url": f"{API_BASE_URL}/pay/{record.payment_id}",
        "execute_url": f"{API_BASE_URL}/execute/{record.payment_id}",
    }


@app.get("/budget/{user_id}")
async def get_budget(user_id: str) -> dict[str, int]:
    """Per-user spend budget the agent checks before paying."""
    spent = await store.total_spent_atomic(user_id)
    remaining = max(0, DEFAULT_BUDGET_ATOMIC - spent)
    return {
        "limit_atomic": DEFAULT_BUDGET_ATOMIC,
        "spent_atomic": spent,
        "remaining_atomic": remaining,
    }


@app.get("/supported")
async def supported() -> Any:
    return facilitator_client.get_supported()


@app.get("/settlements")
async def settlements(limit: int = 6) -> dict[str, Any]:
    """Recent real settlements for the public proof wall, newest first.

    The landing page polls this so the "Real USDC, moving on Arc" list reflects
    fresh on-chain activity instead of a fixed set of hashes. Only PAID records
    with a real tx hash are returned, from the same store the traction report reads.
    """
    limit = max(1, min(limit, 25))
    records = await store.recent_settlements(limit)
    rows = [
        {
            # Normalize to 0x so the explorer link resolves. Older demo rows were
            # written before /execute started prefixing the settle hash.
            "tx_hash": r.tx_hash if r.tx_hash.startswith("0x") else "0x" + r.tx_hash,
            "command": r.command_name,
            "amount_atomic": r.price_atomic,
            "amount_usdc": f"{r.price_atomic / 1_000_000:.4f}",
            "result": r.result,
            "source": "web" if r.guild_id == "web" else "discord",
            "paid_at": r.paid_at.isoformat() if r.paid_at else "",
        }
        for r in records
    ]
    return {"count": len(rows), "settlements": rows}


@app.post("/demo/ask")
async def demo_ask(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Public browser demo: run the real agent loop and settle on Arc.

    Rate-limited per IP with a global spend cap so public traffic can never drain
    the agent wallet. Returns the real decision, answer, spend and Arc tx hash, so
    the landing page shows a fresh on-chain settlement on every paid ask.
    """
    from ..agent import planner

    ip = request.client.host if request.client else "unknown"
    if not _demo_rate_ok(ip, time.time()):
        raise HTTPException(429, "Slow down a moment, then try again.")

    prompt = str(body.get("prompt", "")).strip()[:240]
    if not prompt:
        return {"action": "free", "answer": "Ask me something.", "spent_atomic": 0}

    # Once the demo hits its global cap, keep answering but stop spending.
    capped = _demo_spent["atomic"] >= DEMO_TOTAL_CAP_ATOMIC
    budget = 0 if capped else DEMO_REQUEST_BUDGET_ATOMIC

    decision = await planner.decide(prompt, budget)

    if decision.action != "pay" or decision.tool is None:
        if decision.action == "decline":
            return {"action": "decline", "reason": decision.reason, "spent_atomic": 0}
        answer = await planner.answer_free(prompt)
        return {"action": "free", "reason": decision.reason, "answer": answer, "spent_atomic": 0}

    tool = decision.tool
    arg_val = decision.args.get(tool.arg_name) or prompt
    record = PaymentRecord(
        guild_id="web",
        channel_id="demo",
        user_id="web-demo",
        command_name=tool.command,
        command_args={tool.arg_name: arg_val},
        price_atomic=tool.price_atomic,
    )
    await store.create_payment(record)

    from ..bot.payer import build_paying_client, pay_and_execute

    payer = build_paying_client()
    try:
        result = await pay_and_execute(payer, record.payment_id)
    finally:
        await payer.aclose()

    _demo_spent["atomic"] += tool.price_atomic
    answer = await planner.compose(prompt, tool.name, result.get("result", ""))
    return {
        "action": "pay",
        "reason": decision.reason,
        "tool": tool.name,
        "answer": answer,
        "spent_atomic": tool.price_atomic,
        "tx_hash": result.get("tx_hash", ""),
    }
