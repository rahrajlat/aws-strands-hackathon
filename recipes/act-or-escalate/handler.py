"""Act-or-Escalate: route requests to a small or frontier model via Strands Decider 2B.
Pattern: model routing (#model-routing).

    from handler import make_router
    from strands import Agent
    from strands.models import ModelRouter, RoutingCandidate

    router = ModelRouter(
        [RoutingCandidate(small, name="small"), RoutingCandidate(frontier, name="frontier")],
        strategy=make_router(decider, small, frontier),
    )
    agent = Agent(model=router)

See recipe.yaml for the question wording and measured accuracy (36/40, 123 ms median).
Needs strands-agents and a running server: strands-decider serve <checkpoint> --port 8099.
"""
from pathlib import Path

from decider_framework import YamlRoutingStrategy

YAML = Path(__file__).parent.parent.parent / "decisions" / "act_or_escalate.yaml"


def make_router(decider, small_model, frontier_model, threshold: float = 0.70):
    """Return a Strands RoutingStrategy backed by the act-or-escalate YAML config."""
    return YamlRoutingStrategy.from_yaml(YAML, decider, small_model, frontier_model, threshold)
