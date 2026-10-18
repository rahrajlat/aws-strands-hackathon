"""Stale Order Check: block a tool call that Decider is confident is no longer valid.
Pattern: guardrails (#guardrails).

    from handler import make_guard
    from strands import Agent

    guard = make_guard(decider, state_fn=describe_world)
    agent = Agent(model=model, tools=[my_tool], interventions=[guard])

`state_fn(event) -> str` receives the Strands BeforeToolCallEvent and returns the current
world state as prose Decider can reason over. Omit it to let the handler use the tool name
and arguments instead.

See recipe.yaml for the question wording and measured accuracy (9/12 stale blocked, 0/12 valid blocked).
Needs strands-agents and a running server: strands-decider serve <checkpoint> --port 8099.
"""
from pathlib import Path
from typing import Any, Callable

from decider_framework import YamlInterventionHandler

YAML = Path(__file__).parent.parent.parent / "decisions" / "stale_order_check.yaml"


def make_guard(
    decider,
    state_fn: Callable[[Any], str] | None = None,
    threshold: float | None = None,
):
    """Return a Strands InterventionHandler backed by the stale-order-check YAML config."""
    return YamlInterventionHandler.from_yaml(YAML, decider, threshold=threshold, state_fn=state_fn)
