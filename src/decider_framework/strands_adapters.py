"""Strands wiring: build InterventionHandler and ModelRouter.RoutingStrategy from YAML.

These let any Strands agent use a YAML decision config with one line:

    from decider_framework import YamlInterventionHandler, YamlRoutingStrategy

    guard  = YamlInterventionHandler.from_yaml("decisions/stale_order_check.yaml", model)
    router = YamlRoutingStrategy.from_yaml("decisions/act_or_escalate.yaml", model)

Both are drop-in compatible with the Strands InterventionHandler and RoutingStrategy
protocols respectively.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from decider_framework.engine import DecisionEngine, EngineResult
from decider_framework.schema import DecisionConfig


# ---------------------------------------------------------------------------
# InterventionHandler  (guardrail pattern)
# ---------------------------------------------------------------------------

class YamlInterventionHandler:
    """A Strands InterventionHandler driven by a YAML decision config.

    The 'action' in the YAML policy maps to:
        "proceed"  → Proceed
        "deny"     → Deny(reason=...)
        anything else → Deny (with the action as the reason label)

    Example YAML pattern: stale-order-check, act-or-skip, content-gate.
    """

    name: str

    def __init__(self, config: DecisionConfig, model: Any, threshold: float | None = None) -> None:
        self.config = config
        self.name = f"decider-{config.name}"
        self._engine = DecisionEngine(config)
        self._model = model
        self._threshold = threshold

    @classmethod
    def from_yaml(cls, path: str | Path, model: Any, threshold: float | None = None) -> "YamlInterventionHandler":
        return cls(DecisionConfig.from_yaml(path), model, threshold)

    def before_tool_call(self, event: Any, **kwargs: Any) -> Any:
        """Called by Strands before every tool call. Returns Proceed, Deny, or Guide."""
        try:
            from strands.handlers.intervention import Proceed, Deny  # type: ignore[import]
        except ImportError:
            return None  # strands not installed; allow everything

        tool_name = getattr(event, "tool_name", "unknown")
        tool_input = getattr(event, "tool_input", {})

        state = {
            "tool": tool_name,
            "input": json.dumps(tool_input, default=str)[:500],
        }

        try:
            result = self._engine.decide(self._model, state, threshold=self._threshold)
        except Exception:  # noqa: BLE001 - a guardrail must never crash the agent
            return Proceed()

        if result.action in ("proceed", "allow", "yes"):
            return Proceed()

        reason = f"[{self.name}] {result.action}: confidence {result.confidence:.2f}"
        if result.rule_name:
            reason += f" (rule: {result.rule_name})"
        return Deny(reason=reason)


# ---------------------------------------------------------------------------
# RoutingStrategy  (act-or-escalate pattern)
# ---------------------------------------------------------------------------

class YamlRoutingStrategy:
    """A Strands ModelRouter.RoutingStrategy driven by a YAML decision config.

    The YAML policy should produce two actions:
        "small"    → route to the small / local model
        "frontier" → route to the frontier model

    Any other action name that does not map to a model is treated as "frontier".
    """

    def __init__(
        self,
        config: DecisionConfig,
        model: Any,
        small_model: Any,
        frontier_model: Any,
        threshold: float | None = None,
    ) -> None:
        self.config = config
        self._engine = DecisionEngine(config)
        self._decider = model
        self._small = small_model
        self._frontier = frontier_model
        self._threshold = threshold

    @classmethod
    def from_yaml(
        cls,
        path: str | Path,
        model: Any,
        small_model: Any,
        frontier_model: Any,
        threshold: float | None = None,
    ) -> "YamlRoutingStrategy":
        return cls(DecisionConfig.from_yaml(path), model, small_model, frontier_model, threshold)

    async def select(self, context: Any, **kwargs: Any) -> Any:
        """Called by Strands ModelRouter to pick which model handles this request."""
        state: dict[str, Any] = {}
        if hasattr(context, "messages"):
            last = context.messages[-1] if context.messages else None
            if last is not None:
                content = getattr(last, "content", "")
                state["request"] = str(content)[:500]

        try:
            result = self._engine.decide(self._decider, state, threshold=self._threshold)
        except Exception:  # noqa: BLE001 - routing failure falls back to frontier
            return self._frontier

        if result.action in ("small", "local", "no"):
            return self._small
        return self._frontier

    # Sync variant for callers that don't use async
    def select_sync(self, state: dict[str, Any]) -> Any:
        try:
            result = self._engine.decide(self._decider, state, threshold=self._threshold)
        except Exception:  # noqa: BLE001
            return self._frontier
        if result.action in ("small", "local", "no"):
            return self._small
        return self._frontier
