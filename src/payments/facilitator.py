"""Embedded x402 facilitator for Arc testnet.

Runs in the same process as the FastAPI app — no HTTP round-trip to Circle Gateway.
Uses FacilitatorWeb3Signer with the relayer (facilitator) private key to submit
transferWithAuthorization on Arc testnet USDC and pay the gas. The agent that signed
the EIP-3009 authorization never sends a transaction itself.
"""

from __future__ import annotations

from x402 import x402Facilitator
from x402.mechanisms.evm.exact.register import register_exact_evm_facilitator
from x402.mechanisms.evm.signers import FacilitatorWeb3Signer
from x402.schemas import (
    PaymentPayload,
    PaymentPayloadV1,
    PaymentRequirements,
    PaymentRequirementsV1,
    SettleResponse,
    SupportedResponse,
    VerifyResponse,
)

from .config import ARC_NETWORK, ARC_RPC_URL, FACILITATOR_PRIVATE_KEY


def build_facilitator() -> x402Facilitator:
    """Create and configure the embedded x402Facilitator for Arc testnet."""
    if not FACILITATOR_PRIVATE_KEY:
        raise RuntimeError("FACILITATOR_PRIVATE_KEY not set in environment")

    signer = FacilitatorWeb3Signer(
        private_key=FACILITATOR_PRIVATE_KEY,
        rpc_url=ARC_RPC_URL,
    )

    facilitator = x402Facilitator()
    register_exact_evm_facilitator(
        facilitator,
        signer,
        networks=ARC_NETWORK,
    )
    return facilitator


class EmbeddedFacilitatorClient:
    """Adapts x402Facilitator to the FacilitatorClient protocol.

    Lets x402ResourceServer call verify/settle without HTTP.
    """

    def __init__(self, facilitator: x402Facilitator) -> None:
        self._f = facilitator

    async def verify(
        self,
        payload: PaymentPayload | PaymentPayloadV1,
        requirements: PaymentRequirements | PaymentRequirementsV1,
    ) -> VerifyResponse:
        return await self._f.verify(payload, requirements)

    async def settle(
        self,
        payload: PaymentPayload | PaymentPayloadV1,
        requirements: PaymentRequirements | PaymentRequirementsV1,
    ) -> SettleResponse:
        return await self._f.settle(payload, requirements)

    def get_supported(self) -> SupportedResponse:
        return self._f.get_supported()
