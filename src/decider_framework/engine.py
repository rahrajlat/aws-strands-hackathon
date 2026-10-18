"""DecisionEngine: load a YAML config and run decisions against Strands Decider 2B.

Usage
-----
    engine = DecisionEngine.from_yaml("decisions/hazard_triage.yaml")

    result = engine.decide(
        model=decider_2b,
        state=rover.snapshot(),
        context={"shield_ready": True, "positional": True},
        threshold=0.70,
    )
    print(result.action, result.confidence)
    if result.escalated:
        # confidence was below threshold and waiting was safe — ask Earth

The `model` argument is anything that implements:
    def decide_many(state, questions) -> list[Decision]
which includes Decider2B, MockDecider, and any Protocol-compatible object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from decider_framework.schema import DecisionConfig, QuestionSpec


# ---------------------------------------------------------------------------
# Protocol — matches roverthinking.decisions.model.DecisionModel exactly so
# the engine works with the game's existing clients without importing them.
# ---------------------------------------------------------------------------

@runtime_checkable
class _Question(Protocol):
    key: str
    text: str
    options: dict[str, str]
    kind: str


@runtime_checkable
class _Decision(Protocol):
    question: str
    choice: str
    confidence: float
    probabilities: dict[str, float]
    latency_ms: float
    source: str


class _DecisionModel(Protocol):
    name: str

    def decide_many(self, state: dict[str, Any], questions: list) -> list: ...


# ---------------------------------------------------------------------------
# Public output type
# ---------------------------------------------------------------------------

@dataclass
class EngineResult:
    """The engine's verdict: an action, how confident it is, and the per-question answers."""
    action: str
    confidence: float
    can_wait: bool
    can_wait_confidence: float  # confidence of the can_wait answer (0.5 if no escalation q)
    escalated: bool
    source: str
    latency_ms: float
    detail: tuple[dict[str, Any], ...]  # one entry per question asked (not including escalation q)
    rule_name: str = ""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class DecisionEngine:
    """Runs a YAML decision config against a Strands Decider 2B-compatible model."""

    def __init__(self, config: DecisionConfig) -> None:
        self.config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DecisionEngine":
        return cls(DecisionConfig.from_yaml(path))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DecisionEngine":
        return cls(DecisionConfig.from_dict(raw))

    # -----------------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------------

    def decide(
        self,
        model: Any,
        state: dict[str, Any],
        context: dict[str, Any] | None = None,
        threshold: float | None = None,
    ) -> EngineResult:
        """Run all questions against `model`, apply policy rules, return the action.

        `context` is checked by rules that have a `require_context` key: the rule is
        skipped if context[require_context] is falsy. Typical keys: "shield_ready",
        "positional" (rover can reroute around a hazard).

        `threshold` overrides the policy's own threshold.
        """
        ctx = context or {}
        thr = threshold if threshold is not None else self.config.policy.threshold

        # Build Question objects the model understands
        q_specs = list(self.config.all_questions())
        questions = [_build_question(spec) for spec in q_specs]

        # Ask all questions in one batched request
        decisions = model.decide_many(state, questions)

        latency = max((d.latency_ms for d in decisions), default=0.0)
        answers: dict[str, Any] = {d.question: d for d in decisions}

        # Separate out the escalation answer if present
        esc_spec = self.config.escalation
        can_wait_d = answers.get(esc_spec.key) if esc_spec else None
        can_wait = bool(can_wait_d and can_wait_d.choice == "yes") if can_wait_d else False
        can_wait_conf = float(can_wait_d.confidence) if can_wait_d else 0.5

        # Apply policy rules
        action, conf, rule_name = self._apply_rules(answers, ctx, decisions)

        # Escalation: below threshold AND waiting is safe AND escalation is configured
        escalated = conf < thr and can_wait and esc_spec is not None

        source = decisions[0].source if decisions else "unknown"

        detail = tuple(
            {
                "key": spec.key,
                "short": spec.short or spec.key,
                "text": spec.text,
                "answer": answers[spec.key].choice if spec.key in answers else "?",
                "confidence": round(answers[spec.key].confidence, 3) if spec.key in answers else 0.0,
            }
            for spec in self.config.questions
        )

        return EngineResult(
            action=action,
            confidence=round(conf, 3),
            can_wait=can_wait,
            can_wait_confidence=round(can_wait_conf, 3),
            escalated=escalated,
            source=source,
            latency_ms=round(latency, 1),
            detail=detail,
            rule_name=rule_name,
        )

    def as_questions(self, include_escalation: bool = True) -> list:
        """Return Question objects for all questions in this config (for use with decide_many)."""
        specs = self.config.all_questions() if include_escalation else self.config.questions
        return [_build_question(s) for s in specs]

    # -----------------------------------------------------------------------
    # Policy engine
    # -----------------------------------------------------------------------

    def _apply_rules(
        self,
        answers: dict[str, Any],
        ctx: dict[str, Any],
        decisions: list,
    ) -> tuple[str, float, str]:
        """First matching rule wins. Returns (action, confidence, rule_name)."""
        for rule in self.config.policy.rules:
            # Check context guard
            if rule.require_context and not ctx.get(rule.require_context):
                continue

            # Check all when-conditions
            if not rule.when:
                return rule.then, _conf_for_default(decisions), rule.name

            if _conditions_met(rule.when, answers):
                conf = _conf_for_rule(rule.when, answers)
                return rule.then, conf, rule.name

        # Default fallback
        default_action = self.config.policy.default
        conf = _conf_for_default(decisions)
        return default_action, conf, "default"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_question(spec: QuestionSpec):
    """Build a Question object compatible with DecisionModel.decide_many().

    We import lazily so the framework works standalone without the game.
    """
    try:
        from roverthinking.decisions.model import Question, yes_no  # type: ignore[import]

        if spec.type == "yesno":
            return yes_no(spec.key, spec.text, spec.yes, spec.no, short=spec.short)
        return Question(spec.key, spec.text, spec.options, kind="choice", short=spec.short)
    except ImportError:
        pass

    # Fallback: a plain object the Decider2B client can also use
    return _SimpleQuestion(spec)


@dataclass
class _SimpleQuestion:
    """Minimal Question duck-type when roverthinking is not installed."""
    _spec: QuestionSpec

    @property
    def key(self) -> str:
        return self._spec.key

    @property
    def text(self) -> str:
        return self._spec.text

    @property
    def short(self) -> str:
        return self._spec.short

    @property
    def kind(self) -> str:
        return "yesno" if self._spec.type == "yesno" else "choice"

    @property
    def options(self) -> dict[str, str]:
        if self._spec.type == "yesno":
            return {"yes": self._spec.yes, "no": self._spec.no}
        return self._spec.options


def _conditions_met(when: dict[str, str], answers: dict[str, Any]) -> bool:
    for key, expected in when.items():
        d = answers.get(key)
        if d is None:
            return False
        if d.choice != expected:
            return False
    return True


def _conf_for_rule(when: dict[str, str], answers: dict[str, Any]) -> float:
    """Confidence for a matched rule = min confidence of its matching conditions."""
    confs = []
    for key, expected in when.items():
        d = answers.get(key)
        if d is None:
            continue
        # If the answer matches the expected value, use its confidence.
        # If it's a "no" condition, the confidence is (1 - p_yes).
        if d.choice == expected:
            confs.append(d.confidence)
        else:
            confs.append(1 - d.confidence)
    return min(confs) if confs else 0.5


def _conf_for_default(decisions: list) -> float:
    """Confidence when the default rule fires = min of all answer confidences."""
    if not decisions:
        return 0.5
    return min(d.confidence for d in decisions)
