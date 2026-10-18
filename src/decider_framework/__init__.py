"""decider_framework — YAML-driven decision patterns for Strands Decider 2B.

Drop a decision YAML file into your project, point DecisionEngine at it, and get
structured decisions backed by Decider 2B with automatic escalation and Strands wiring.

    engine = DecisionEngine.from_yaml("decisions/hazard_triage.yaml")
    result = engine.decide(model, state, context={"shield_ready": True})
    print(result.action, result.confidence)

For Strands agents:
    guard  = YamlInterventionHandler.from_yaml("decisions/stale_order_check.yaml", model)
    router = YamlRoutingStrategy.from_yaml("decisions/act_or_escalate.yaml", model)
"""

from decider_framework.engine import DecisionEngine, EngineResult
from decider_framework.schema import DecisionConfig, PolicySpec, QuestionSpec, RuleSpec
from decider_framework.strands_adapters import YamlInterventionHandler, YamlRoutingStrategy

__all__ = [
    "DecisionConfig",
    "DecisionEngine",
    "EngineResult",
    "PolicySpec",
    "QuestionSpec",
    "RuleSpec",
    "YamlInterventionHandler",
    "YamlRoutingStrategy",
]
