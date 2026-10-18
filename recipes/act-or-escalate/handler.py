"""Act-or-Escalate: let Strands Decider 2B decide whether the small model can answer, or the request
must be escalated to a frontier model.  Pattern: model routing.

Drop-in for Strands' built-in `ClassifierStrategy`, which asks an LLM which model to use on every
request. This strategy asks a local Decider 2B instead: about a hundred milliseconds, no tokens spent,
and a calibrated confidence. Act on the small model when Decider is confident it is enough; escalate
to the frontier model when Decider says it is not, or when Decider is unsure.

    from strands import Agent
    from strands.models import ModelRouter, RoutingCandidate
    from handler import DeciderRoutingStrategy

    router = ModelRouter(
        [RoutingCandidate(small_model, name="small"), RoutingCandidate(frontier_model, name="frontier")],
        strategy=DeciderRoutingStrategy(),
    )
    agent = Agent(model=router)

Needs only `strands-agents` and a running server: `strands-decider serve <checkpoint> --port 8099`.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

# ---- the typed question and its options ---------------------------------------------------------

QUESTION_KEY = "needs_frontier"
QUESTION = (
    "Does answering this request need a frontier model? Say yes if it needs multi-step reasoning, "
    "careful planning, long or nuanced writing, specialist knowledge, or handling an upset customer; "
    "say no if it is a short factual lookup, a simple instruction, or small talk."
)
YES = (
    "needs deep reasoning, planning, nuance or specialist knowledge: a small model would likely get it wrong"
)
NO = "a short, simple, well-defined request that a small model answers correctly"

DEFAULT_THRESHOLD = 0.70  # below this confidence, escalate rather than gamble on the small model


# ---- a tiny Decider client (stdlib only, so this file drops in anywhere) -------------------------


@dataclass(frozen=True)
class YesNo:
    answer: bool  # Decider's pick
    p_yes: float  # probability of yes
    confidence: float  # certainty of the pick: max(p_yes, 1 - p_yes)
    latency_ms: float


class DeciderClient:
    """Asks a yes/no question of a locally served Decider 2B (POST /v1/systemone)."""

    def __init__(self, url: str | None = None, timeout: float = 30.0) -> None:
        self.url = (url or os.environ.get("STRANDS_DECIDER_URL", "http://127.0.0.1:8099")).rstrip("/")
        self.timeout = timeout

    def ask_yes_no(self, state: str, key: str, question: str, yes: str, no: str) -> YesNo:
        body = {
            "state": state,
            "model": "recipe",
            "questions": {
                key: {"type": "noul", "instructions": question, "criteria": {"true": yes, "false": no}}
            },
        }
        req = urllib.request.Request(
            f"{self.url}/v1/systemone",
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
        )
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read())
        wall = (time.perf_counter() - start) * 1000
        p = float(payload["answers"][key]["noul"])
        return YesNo(p >= 0.5, p, max(p, 1 - p), float(payload.get("latency_ms") or wall))


# ---- the routing strategy -------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingDecision:
    """What was decided, for logs and benchmarks."""

    chose: str
    reason: str  # "confident-small" | "needs-frontier" | "unsure-escalate" | "decider-down" | "failover"
    needs_frontier: bool | None
    confidence: float | None
    latency_ms: float | None
    request: str


def latest_user_text(messages: Sequence[dict[str, Any]]) -> str:
    """The text of the most recent user message."""
    for message in reversed(messages):
        if message.get("role") == "user":
            parts = [b["text"] for b in message.get("content", []) if isinstance(b, dict) and "text" in b]
            if parts:
                return "\n".join(parts)
    return ""


class DeciderRoutingStrategy:
    """A Strands `RoutingStrategy` whose decision is made by Decider 2B instead of an LLM."""

    def __init__(
        self,
        small: str = "small",
        frontier: str = "frontier",
        *,
        client: DeciderClient | Any | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        question: str = QUESTION,
        on_decision: Callable[[RoutingDecision], None] | None = None,
    ) -> None:
        self.small, self.frontier = small, frontier
        self.client = client or DeciderClient()
        self.threshold = threshold
        self.question = question
        self.on_decision = on_decision

    async def select(self, context: Any, **kwargs: Any) -> Any:
        by_name = {c.name: c for c in context.candidates}
        small, frontier = by_name[self.small], by_name[self.frontier]

        if context.attempts:  # a model call failed: fail over to whichever one has not been tried
            used = {id(a.candidate) for a in context.attempts}
            nxt = next((c for c in (small, frontier) if id(c) not in used), None)
            self._log(nxt.name if nxt else "none", "failover", None, None, None, "")
            return nxt

        text = latest_user_text(context.messages)
        try:
            result = await asyncio.to_thread(
                self.client.ask_yes_no, text, QUESTION_KEY, self.question, YES, NO
            )
        except Exception:  # noqa: BLE001 - if Decider is unreachable, play safe and escalate
            self._log(frontier.name, "decider-down", None, None, None, text)
            return frontier

        if result.answer:
            chosen, reason = frontier, "needs-frontier"
        elif result.confidence < self.threshold:
            chosen, reason = frontier, "unsure-escalate"
        else:
            chosen, reason = small, "confident-small"
        self._log(chosen.name, reason, result.answer, result.confidence, result.latency_ms, text)
        return chosen

    def _log(self, chose, reason, needs, conf, latency, text) -> None:
        if self.on_decision is not None:
            self.on_decision(RoutingDecision(chose, reason, needs, conf, latency, text))
