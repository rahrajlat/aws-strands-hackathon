"""Stale Order Check: before a tool call runs, ask Strands Decider 2B whether it is still valid and safe
given the *current* state. Pattern: guardrails.

Agents often act on a decision made a while ago: a queued approval, a plan from a minute back, an order
that crossed a slow link. By the time the tool runs the world may have changed. This is a Strands
`InterventionHandler` that asks Decider "given the state right now, is this call still valid and safe?"
and blocks the call (or steers the model) when the answer is a confident no.

    from strands import Agent
    from handler import StaleCallGuard

    guard = StaleCallGuard(state_fn=lambda event: describe_world(), tools={"issue_refund"})
    agent = Agent(model=model, tools=[issue_refund], interventions=[guard])

Needs only `strands-agents` and a running server: `strands-decider serve <checkpoint> --port 8099`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from strands.interventions import Deny, Guide, InterventionHandler, Proceed

# ---- the typed question and its options ---------------------------------------------------------

QUESTION_KEY = "still_valid"
QUESTION = "Is the thing this tool call acts on still in the state the call expects?"
YES = "yes: still in the expected state, so the call is still appropriate"
NO = (
    "no: it has already been done, cancelled, closed, expired, refunded, paid or become "
    "unavailable, so the call is no longer appropriate"
)

# Block when Decider's answer is "no" with at least this confidence. 0.50 means any "no". Measured on
# 24 labelled cases (see README): 0.50 catches 9/12 stale calls, 0.60 catches 8/12, 0.70 catches 6/12,
# and none of them blocked a valid call. Raise it if a false block is costly.
DEFAULT_BLOCK_CONFIDENCE = 0.50


# ---- a tiny Decider client (stdlib only, so this file drops in anywhere) -------------------------


@dataclass(frozen=True)
class YesNo:
    answer: bool
    p_yes: float
    confidence: float
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


# ---- the handler ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardDecision:
    """What was decided, for logs and benchmarks."""

    tool: str
    arguments: dict[str, Any]
    valid: bool
    confidence: float
    latency_ms: float
    blocked: bool


class StaleCallGuard(InterventionHandler):
    """Blocks a tool call that Decider is confident is no longer valid given the current state."""

    name = "decider-stale-call-guard"

    def __init__(
        self,
        state_fn: Callable[[Any], str],
        *,
        tools: set[str] | None = None,
        client: DeciderClient | Any | None = None,
        block_confidence: float = DEFAULT_BLOCK_CONFIDENCE,
        action: str = "deny",
        fail_closed: bool = True,
        on_decision: Callable[[GuardDecision], None] | None = None,
    ) -> None:
        """
        state_fn: returns the *current* state as plain text; called with the BeforeToolCallEvent.
        tools: only guard these tool names (default: every tool).
        action: "deny" blocks the call; "guide" sends the model feedback so it can replan.
        fail_closed: if Decider cannot be reached, block the call (safest for side-effect tools).
        """
        self.state_fn = state_fn
        self.tools = tools
        self.client = client or DeciderClient()
        self.block_confidence = block_confidence
        self.action = action
        self._fail_closed = fail_closed
        self.on_decision = on_decision

    @property
    def on_error(self) -> str:  # what Strands does if this handler raises (e.g. Decider is down)
        return "deny" if self._fail_closed else "proceed"

    def before_tool_call(self, event: Any, **kwargs: Any):
        tool = event.tool_use["name"]
        if self.tools is not None and tool not in self.tools:
            return Proceed()
        args = event.tool_use.get("input") or {}
        state = (
            f"{self.state_fn(event)}\n\n"
            f"The agent now wants to run the tool '{tool}' with arguments {json.dumps(args)}."
        )
        result = self.client.ask_yes_no(state, QUESTION_KEY, QUESTION, YES, NO)
        blocked = (not result.answer) and result.confidence >= self.block_confidence
        if self.on_decision is not None:
            self.on_decision(
                GuardDecision(tool, dict(args), result.answer, result.confidence, result.latency_ms, blocked)
            )
        if not blocked:
            return Proceed(reason=f"still valid ({result.confidence:.2f})")
        reason = (
            f"Blocked '{tool}': the situation changed since this call was planned "
            f"(Decider is {result.confidence:.0%} sure it is no longer appropriate)."
        )
        if self.action == "guide":
            return Guide(feedback=reason + " Re-check the current state and explain to the user.")
        return Deny(reason=reason)
