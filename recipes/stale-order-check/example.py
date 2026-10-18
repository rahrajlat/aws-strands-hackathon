"""A refund bot whose tool call can go stale: the guard stops a refund that no longer makes sense.

    uv run python example.py --offline      # no AWS, no model server: a scripted model and a keyword "decider"
    uv run python example.py                # real: Decider 2B on :8099 + an Amazon Bedrock model

Live mode reads MODEL (a Bedrock model ID, default anthropic.claude-haiku-5-5) and AWS_REGION.
"""

from __future__ import annotations

import argparse
import os

from handler import GuardDecision, StaleCallGuard, YesNo
from strands import Agent, tool
from strands.models.model import Model

ORDERS = {
    "1001": {"status": "delivered", "refunded": False, "amount": 40.0},
    "1002": {"status": "delivered", "refunded": True, "amount": 25.0},  # someone already refunded it
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
    """The current state of the order the tool call is about: what a human would check first."""
    o = ORDERS.get(str(event.tool_use.get("input", {}).get("order_id")), {})
    return (
        f"Order status: {o.get('status', 'unknown')}. "
        f"This order {'HAS ALREADY BEEN REFUNDED' if o.get('refunded') else 'has not been refunded yet'}. "
        f"Order amount: {o.get('amount', 0):.2f}."
    )


class ScriptedModel(Model):
    """A stand-in Strands model: asks to refund the order the user names, then reports the outcome."""

    def update_config(self, **model_config):  # noqa: D102
        pass

    def get_config(self):  # noqa: D102
        return {"model_id": "scripted"}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # noqa: D102
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):  # noqa: D102
        last = messages[-1]
        results = [b["toolResult"] for b in last.get("content", []) if "toolResult" in b]
        yield {"messageStart": {"role": "assistant"}}
        yield (
            {"contentBlockStart": {"start": {}}}
            if results
            else {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "t1", "name": "issue_refund"}}}}
        )
        if results:
            text = results[0]["content"][0].get("text", "")
            yield {"contentBlockDelta": {"delta": {"text": f"Result: {text}"}}}
            stop = "end_turn"
        else:
            order = next(w.strip(".,") for w in last["content"][0]["text"].split() if w.strip(".,").isdigit())
            args = f'{{"order_id": "{order}", "amount": {ORDERS[order]["amount"]}}}'
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": args}}}}
            stop = "tool_use"
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": stop}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
                "metrics": {"latencyMs": 1},
            }
        }


class KeywordDecider:
    """Offline stand-in for Decider 2B: an already-refunded order makes a refund invalid."""

    def ask_yes_no(self, state, key, question, yes, no) -> YesNo:
        stale = "ALREADY BEEN REFUNDED" in state
        return YesNo(not stale, 0.1 if stale else 0.9, 0.9, 1.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    log: list[GuardDecision] = []
    if args.offline:
        model, client = ScriptedModel(), KeywordDecider()
    else:
        from handler import DeciderClient
        from strands.models import BedrockModel

        model = BedrockModel(
            model_id=os.environ.get("MODEL", "anthropic.claude-haiku-5-5"),
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
        client = DeciderClient()
    guard = StaleCallGuard(describe_world, tools={"issue_refund"}, client=client, on_decision=log.append)
    agent = Agent(
        model=model,
        tools=[issue_refund],
        interventions=[guard],
        callback_handler=None,
        system_prompt="You are a refund assistant. Use the issue_refund tool when asked.",
    )
    for order in ("1001", "1002"):
        reply = agent(f"Please refund order {order}.")
        d = log[-1]
        verdict = "BLOCKED " if d.blocked else "allowed "
        print(f"order {order}: {verdict} (confidence {d.confidence:.2f}, decided in {d.latency_ms:.0f} ms)")
        print(f"   {str(reply)[:110]}")


if __name__ == "__main__":
    main()
