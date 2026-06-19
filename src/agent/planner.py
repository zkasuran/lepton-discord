"""The agent's brain: decide whether to spend, on which tool, within budget.

This is the agentic core. Given a free-form request and the USDC left in the
user's budget, the agent (Claude, via tool-use) picks the single cheapest tool
that genuinely answers the request, or chooses to answer for free. The budget is
then enforced in code, not left to the model: an over-budget pick is declined.

The Claude calls are isolated in `_plan`, `compose` and `answer_free` so tests
monkeypatch them and stay offline. The agent's own reasoning is free; it only
spends USDC on the external paid tools it decides to call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from ..payments.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL
from .tools import TOOL_CATALOG, ToolSpec, get_tool

logger = logging.getLogger("nanopay.planner")

Action = Literal["pay", "decline", "answer_free"]

# Anthropic tool name for "do not buy anything, answer for free".
_RESPOND_DIRECTLY = "respond_directly"


def _norm(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())


def _resolve_tool_name(returned: str, catalog: list[ToolSpec]) -> str | None:
    """Map a tool name returned by the model back to a catalog name.

    Tolerates provider-side name transforms (camel-casing, suffixes) by matching
    on a normalized prefix. Returns None for the respond-directly sentinel.
    """
    n = _norm(returned)
    if _norm(_RESPOND_DIRECTLY) in n:
        return None
    for tool in catalog:
        if _norm(tool.name) in n:
            return tool.name
    return returned  # unknown; caller handles the miss


@dataclass
class Decision:
    action: Action
    reason: str
    tool: ToolSpec | None = None
    args: dict[str, str] = field(default_factory=dict)
    est_cost_atomic: int = 0


async def decide(
    prompt: str,
    budget_remaining_atomic: int,
    catalog: list[ToolSpec] | None = None,
) -> Decision:
    """Plan a response, then enforce the budget in code."""
    catalog = catalog if catalog is not None else TOOL_CATALOG
    plan = await _plan(prompt, budget_remaining_atomic, catalog)

    name = plan.get("tool")
    reason = str(plan.get("reason", "")).strip()
    if not name:
        return Decision("answer_free", reason or "No paid data needed for this.")

    tool = get_tool(str(name))
    if tool is None:
        return Decision("answer_free", f"Planner picked unknown tool '{name}'; answering directly.")

    raw_args = plan.get("args") or {}
    args = {k: str(v) for k, v in raw_args.items()} if isinstance(raw_args, dict) else {}
    cost = tool.price_atomic

    if cost > budget_remaining_atomic:
        left = budget_remaining_atomic / 1_000_000
        msg = f"Declined: {tool.name} ({tool.price_display}) over the ${left:.4f} budget left."
        return Decision("decline", msg, tool=tool, args=args, est_cost_atomic=cost)

    return Decision(
        "pay",
        reason or f"Buying {tool.name} for {tool.price_display}.",
        tool=tool,
        args=args,
        est_cost_atomic=cost,
    )


# ============================================================================
# Claude IO (monkeypatched in tests)
# ============================================================================


def _anthropic_tools(catalog: list[ToolSpec]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for t in catalog:
        tools.append(
            {
                "name": t.name,
                "description": f"{t.description} Costs {t.price_display} USDC per call.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        t.arg_name: {"type": "string", "description": t.arg_description}
                    },
                    "required": [t.arg_name],
                },
            }
        )
    tools.append(
        {
            "name": _RESPOND_DIRECTLY,
            "description": "Answer the user directly for free, without buying any tool.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why no paid tool is needed."}
                },
                "required": ["reason"],
            },
        }
    )
    return tools


async def _plan(
    prompt: str, budget_remaining_atomic: int, catalog: list[ToolSpec]
) -> dict[str, Any]:
    """Ask Claude to pick one tool or respond directly. Returns {tool, args, reason}."""
    if not ANTHROPIC_API_KEY:
        # No brain available: answer for free rather than spend blindly.
        return {"tool": None, "reason": "AI planner unavailable; answering directly."}

    from anthropic import AsyncAnthropic
    from anthropic.types import ToolChoiceAnyParam, ToolParam

    budget = budget_remaining_atomic / 1_000_000
    system = (
        "You are an autonomous agent with a USDC budget that pays for tools when they help. "
        f"You have ${budget:.4f} left. "
        "If the request needs live or real-time data you cannot possibly know (current crypto "
        "prices, current weather) or premium depth beyond a quick reply, buy the single cheapest "
        "tool that delivers it. If you can answer well from your own knowledge (general questions, "
        "chat, explanations), use respond_directly and spend nothing. "
        "Pick exactly one option. Prefer the cheapest tool that does the job. "
        "Even when a tool would help, choose respond_directly if its price exceeds the budget left."
    )
    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=400,
        system=system,
        tools=cast("list[ToolParam]", _anthropic_tools(catalog)),
        tool_choice=cast("ToolChoiceAnyParam", {"type": "any"}),
        messages=[{"role": "user", "content": prompt}],
    )
    for block in msg.content:
        if block.type == "tool_use":
            raw = block.input
            args = raw if isinstance(raw, dict) else {}
            resolved = _resolve_tool_name(block.name, catalog)
            if resolved is None:
                return {"tool": None, "reason": str(args.get("reason", ""))}
            return {"tool": resolved, "args": args, "reason": f"Selected {resolved}."}
    return {"tool": None, "reason": "Planner returned no tool; answering directly."}


async def answer_free(prompt: str) -> str:
    """Agent answers from its own knowledge — free, no USDC spent."""
    if not ANTHROPIC_API_KEY:
        return "AI unavailable: ANTHROPIC_API_KEY not set."
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in msg.content if b.type == "text"]
    return "\n".join(parts).strip() or "(no answer)"


async def compose(prompt: str, tool_name: str, tool_result: str) -> str:
    """Compose a final answer from a paid tool's result. Free reasoning step."""
    if not ANTHROPIC_API_KEY:
        return tool_result
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=600,
        messages=[
            {
                "role": "user",
                "content": (
                    f"User asked: {prompt}\n\n"
                    f"You paid for the '{tool_name}' tool and it returned:\n{tool_result}\n\n"
                    "Write a short, direct answer to the user using this result."
                ),
            }
        ],
    )
    parts = [b.text for b in msg.content if b.type == "text"]
    return "\n".join(parts).strip() or tool_result
