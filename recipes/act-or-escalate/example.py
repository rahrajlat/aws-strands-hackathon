"""Customer-support bot that routes easy requests to a small model and hard ones to frontier.

    uv run python example.py --offline      # no server needed: keyword-based offline mock
    uv run python example.py                # real: Decider 2B on :8099 + Amazon Bedrock

Live mode reads SMALL_MODEL / FRONTIER_MODEL (Bedrock model IDs) and AWS_REGION.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from dataclasses import dataclass, field

from handler import make_router
from strands import Agent
from strands.models import ModelRouter, RoutingCandidate
from strands.models.model import Model

REQUESTS = [
    "What time do you open on Saturdays?",
    "Can you reset my password?",
    "Thanks, that's all!",
    (
        "I was charged twice for two different orders, one of them arrived damaged, and your chat bot "
        "told me last week the refund would be in 3 days. Explain what went wrong, what I am owed, and "
        "write the email I should send to my bank if you cannot fix this."
    ),
    (
        "Compare your premium and basic plans for a 40-person team with strict data-residency needs "
        "and tell me which to buy, with the trade-offs."
    ),
]


# ---------------------------------------------------------------------------
# Shared mock Decision type (matches engine.py's expected interface)
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
# Offline mock: implements decide_many() with a keyword heuristic
# ---------------------------------------------------------------------------

class KeywordDecider:
    """Offline stand-in: long or complaint-like requests need the frontier model."""

    name = "keyword-decider"

    def decide_many(self, state, questions):
        text = state.get("request", "") if isinstance(state, dict) else str(state)
        hard = len(text) > 120 or any(w in text.lower() for w in ("explain", "compare", "charged twice"))
        choice, conf = ("yes", 0.9) if hard else ("no", 0.9)
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
# Scripted stand-in model (labels responses with its own name for offline demos)
# ---------------------------------------------------------------------------

class ScriptedModel(Model):
    def __init__(self, name: str) -> None:
        self.name = name

    def update_config(self, **kw): pass
    def get_config(self): return {"model_id": self.name}
    async def structured_output(self, *a, **kw): raise NotImplementedError

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": f"[answered by {self.name} model]"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 5, "totalTokens": 10},
                            "metrics": {"latencyMs": 1}}}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    if args.offline:
        small, frontier, decider = ScriptedModel("small"), ScriptedModel("frontier"), KeywordDecider()
    else:
        from strands.models import BedrockModel
        region = os.environ.get("AWS_REGION", "us-east-1")
        small = BedrockModel(model_id=os.environ.get("SMALL_MODEL", "anthropic.claude-haiku-5-5"), region_name=region)
        frontier = BedrockModel(model_id=os.environ.get("FRONTIER_MODEL", "anthropic.claude-sonnet-5-5"), region_name=region)
        decider = _Decider2B()

    router = ModelRouter(
        [RoutingCandidate(small, name="small"), RoutingCandidate(frontier, name="frontier")],
        strategy=make_router(decider, small, frontier),
    )
    agent = Agent(
        model=router,
        callback_handler=None,
        system_prompt="You are a concise, friendly customer-support assistant.",
    )

    for text in REQUESTS:
        reply = agent(text)
        print(f"Q: {text[:72]!r}")
        print(f"   {str(reply)[:100]}")
        print()


if __name__ == "__main__":
    main()
