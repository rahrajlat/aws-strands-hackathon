"""Refund bot whose tool call can go stale: the guard stops a refund that no longer makes sense.

    uv run python example.py --offline      # no server needed: keyword-based offline mock
    uv run python example.py                # real: Decider 2B on :8099 + Amazon Bedrock

Live mode reads MODEL (a Bedrock model ID, default anthropic.claude-haiku-5-5) and AWS_REGION.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field

from handler import make_guard
from strands import Agent, tool
from strands.models.model import Model

ORDERS = {
    "1001": {"status": "delivered", "refunded": False, "amount": 40.0},
    "1002": {"status": "delivered", "refunded": True, "amount": 25.0},  # already refunded
    "1003": {"status": "shipped back, awaiting inspection", "refunded": False, "amount": 80.0},
}


@tool
def issue_refund(order_id: str, amount: float) -> str:
    """Refund an order to the customer's original payment method.

    Args:
        order_id: The order number.
        amount: The amount to refund.
    """
    ORDERS[order_id]["refunded"] = True
    return f"Refunded {amount:.2f} on order {order_id}."


def describe_world(event) -> str:
    """The current state of the order — what a human would check before issuing a refund."""
    o = ORDERS.get(str(event.tool_use.get("input", {}).get("order_id")), {})
    return (
        f"Order status: {o.get('status', 'unknown')}. "
        f"This order {'HAS ALREADY BEEN REFUNDED' if o.get('refunded') else 'has not been refunded yet'}. "
        f"Order amount: {o.get('amount', 0):.2f}."
    )


# ---------------------------------------------------------------------------
# Shared mock Decision type
# ---------------------------------------------------------------------------

@dataclass
class _D:
    question: str
    choice: str
    confidence: float
    probabilities: dict = field(default_factory=dict)
    latency_ms: float = 1.0
    source: str = "mock"


# ---------------------------------------------------------------------------
# Offline mock: blocks refunds on already-refunded orders
# ---------------------------------------------------------------------------

class KeywordDecider:
    """Offline stand-in: an already-refunded order makes a refund call invalid."""

    name = "keyword-decider"

    def decide_many(self, state, questions):
        desc = state.get("description", state.get("input", "")) if isinstance(state, dict) else str(state)
        stale = "ALREADY BEEN REFUNDED" in desc
        choice, conf = ("no", 0.9) if stale else ("yes", 0.9)
        return [_D(q.key, choice, conf, {"yes": conf, "no": 1 - conf}) for q in questions]


# ---------------------------------------------------------------------------
# Standalone Decider 2B client for live mode (stdlib only)
# ---------------------------------------------------------------------------

class _Decider2B:
    """Minimal Decider 2B client that implements decide_many() for live use."""

    name = "strands-decider-2b"

    def __init__(self, url: str | None = None) -> None:
        self.url = (url or os.environ.get("STRANDS_DECIDER_URL", "http://127.0.0.1:8099")).rstrip("/")

    def decide_many(self, state, questions):
        payload = {
            "state": json.dumps(state, default=str),
            "model": "recipe",
            "questions": {
                q.key: (
                    {"type": "noul", "instructions": q.text,
                     "criteria": {"true": q.options.get("yes", ""), "false": q.options.get("no", "")}}
                    if q.kind == "yesno"
                    else {"type": "choice", "instructions": q.text, "criteria": q.options}
                )
                for q in questions
            },
        }
        req = urllib.request.Request(
            f"{self.url}/v1/systemone",
            json.dumps(payload).encode(),
            {"content-type": "application/json"},
        )
        t = time.perf_counter()
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        ms = (time.perf_counter() - t) * 1000
        results = []
        for q in questions:
            ans = data["answers"][q.key]
            if q.kind == "yesno":
                p = float(ans["noul"])
                results.append(_D(q.key, "yes" if p >= 0.5 else "no", max(p, 1 - p), {"yes": p, "no": 1 - p}, ms))
            else:
                results.append(_D(q.key, ans["choice"], float(ans["confidence"]),
                                  {k: float(v) for k, v in ans["probabilities"].items()}, ms))
        return results


# ---------------------------------------------------------------------------
# Scripted model for offline demos
# ---------------------------------------------------------------------------

class ScriptedModel(Model):
    """Asks to refund the order named in the user message, then reports the outcome."""

    def update_config(self, **kw): pass
    def get_config(self): return {"model_id": "scripted"}
    async def structured_output(self, *a, **kw): raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        last = messages[-1]
        results = [b["toolResult"] for b in last.get("content", []) if "toolResult" in b]
        yield {"messageStart": {"role": "assistant"}}
        if results:
            yield {"contentBlockStart": {"start": {}}}
            text = results[0]["content"][0].get("text", "")
            yield {"contentBlockDelta": {"delta": {"text": f"Result: {text}"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            order = next(w.strip(".,") for w in last["content"][0]["text"].split() if w.strip(".,").isdigit())
            args = f'{{"order_id": "{order}", "amount": {ORDERS[order]["amount"]}}}'
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "t1", "name": "issue_refund"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": args}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
                            "metrics": {"latencyMs": 1}}}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    if args.offline:
        model, decider = ScriptedModel(), KeywordDecider()
    else:
        from strands.models import BedrockModel
        model = BedrockModel(
            model_id=os.environ.get("MODEL", "anthropic.claude-haiku-5-5"),
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
        decider = _Decider2B()

    guard = make_guard(decider, state_fn=describe_world)
    agent = Agent(
        model=model,
        tools=[issue_refund],
        interventions=[guard],
        callback_handler=None,
        system_prompt="You are a refund assistant. Use the issue_refund tool when asked.",
    )

    for order in ("1001", "1002"):
        reply = agent(f"Please refund order {order}.")
        print(f"order {order}: {str(reply)[:110]}")


if __name__ == "__main__":
    main()
