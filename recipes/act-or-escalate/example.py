"""A customer-support bot that answers easy questions on a small model and escalates hard ones.

    uv run python example.py --offline      # no AWS, no model server: stand-in models and a keyword "decider"
    uv run python example.py                # real: Decider 2B on :8099 + Amazon Bedrock models

Live mode reads SMALL_MODEL / FRONTIER_MODEL (Bedrock model IDs) and AWS_REGION.
"""

from __future__ import annotations

import argparse
import os

from handler import DeciderClient, DeciderRoutingStrategy, RoutingDecision, YesNo
from strands import Agent
from strands.models import ModelRouter, RoutingCandidate
from strands.models.model import Model

REQUESTS = [
    "What time do you open on Saturdays?",
    "Can you reset my password?",
    "Thanks, that's all!",
    "I was charged twice for two different orders, one of them arrived damaged, and your chat bot "
    "told me last week the refund would be in 3 days. Explain what went wrong, what I am owed, and "
    "write the email I should send to my bank if you cannot fix this.",
    "Compare your premium and basic plans for a 40-person team with strict data-residency needs and "
    "tell me which to buy, with the trade-offs.",
]


class ScriptedModel(Model):
    """A stand-in Strands model that replies with its own name, so routing is visible offline."""

    def __init__(self, name: str) -> None:
        self.name = name

    def update_config(self, **model_config):  # noqa: D102
        pass

    def get_config(self):  # noqa: D102
        return {"model_id": self.name}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # noqa: D102
        raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):  # noqa: D102
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": f"[answered by the {self.name} model]"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {
            "metadata": {
                "usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
                "metrics": {"latencyMs": 1},
            }
        }


class KeywordDecider:
    """Offline stand-in for Decider 2B: long or complaint-like requests need the frontier model."""

    def ask_yes_no(self, state, key, question, yes, no) -> YesNo:
        hard = len(state) > 120 or any(w in state.lower() for w in ("explain", "compare", "charged twice"))
        return YesNo(hard, 0.9 if hard else 0.1, 0.9, 1.0)


def build_agent(offline: bool, on_decision) -> Agent:
    if offline:
        small, frontier, client = ScriptedModel("small"), ScriptedModel("frontier"), KeywordDecider()
    else:
        from strands.models import BedrockModel

        region = os.environ.get("AWS_REGION", "us-east-1")
        small = BedrockModel(
            model_id=os.environ.get("SMALL_MODEL", "anthropic.claude-haiku-5-5"), region_name=region
        )
        frontier = BedrockModel(
            model_id=os.environ.get("FRONTIER_MODEL", "anthropic.claude-sonnet-5-5"), region_name=region
        )
        client = DeciderClient()
    router = ModelRouter(
        [RoutingCandidate(small, name="small"), RoutingCandidate(frontier, name="frontier")],
        strategy=DeciderRoutingStrategy(client=client, on_decision=on_decision),
    )
    return Agent(
        model=router,
        callback_handler=None,
        system_prompt="You are a concise, friendly customer-support assistant.",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    log: list[RoutingDecision] = []
    agent = build_agent(args.offline, log.append)
    for text in REQUESTS:
        reply = agent(text)
        d = log[-1]
        conf = f"{d.confidence:.2f}" if d.confidence is not None else "-"
        ms = f"{d.latency_ms:.0f} ms" if d.latency_ms is not None else "-"
        print(f"{d.chose:>8} ({d.reason}, confidence {conf}, decided in {ms})  <- {text[:60]!r}")
        print(f"         {str(reply)[:90]}")


if __name__ == "__main__":
    main()
