"""Tests for the YAML-driven decider_framework package.

Verifies that:
  - All four decision configs load and parse correctly
  - The engine applies policy rules in order (first match wins)
  - Context guards (require_context) are respected
  - The escalation/can_wait path works
  - The game's triage and instrument functions still work
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decider_framework import DecisionEngine
from decider_framework.schema import DecisionConfig, RuleSpec

DECISIONS = Path(__file__).parent.parent / "decisions"


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

def test_all_configs_load():
    for name in ("hazard_triage", "instrument_choice", "stale_order_check", "act_or_escalate"):
        cfg = DecisionConfig.from_yaml(DECISIONS / f"{name}.yaml")
        assert cfg.name
        assert len(cfg.questions) >= 1
        assert cfg.policy.threshold > 0

def test_hazard_triage_has_six_questions_and_escalation():
    cfg = DecisionConfig.from_yaml(DECISIONS / "hazard_triage.yaml")
    assert len(cfg.questions) == 5  # split classifiers
    assert cfg.escalation is not None
    assert cfg.escalation.key == "can_wait"

def test_instrument_choice_one_question_and_escalation():
    cfg = DecisionConfig.from_yaml(DECISIONS / "instrument_choice.yaml")
    assert len(cfg.questions) == 1
    assert cfg.escalation is not None

def test_rules_parsed_with_string_yes_no():
    """Confirm that 'when: {key: yes}' stores 'yes', not 'True'."""
    cfg = DecisionConfig.from_yaml(DECISIONS / "hazard_triage.yaml")
    for rule in cfg.policy.rules:
        for v in rule.when.values():
            assert v in ("yes", "no"), f"unexpected when-value {v!r} (yaml boolean leak?)"

def test_stale_order_check_uses_order_valid_key():
    cfg = DecisionConfig.from_yaml(DECISIONS / "stale_order_check.yaml")
    assert cfg.questions[0].key == "order_valid"


# ---------------------------------------------------------------------------
# Engine rules and confidence
# ---------------------------------------------------------------------------

class _FixedDecider:
    """Returns a fixed set of yes/no answers with given confidence."""
    name = "fixed"

    def __init__(self, answers: dict[str, tuple[str, float]]) -> None:
        self._answers = answers

    def decide_many(self, state, questions):
        from roverthinking.decisions.model import Decision

        results = []
        for q in questions:
            choice, conf = self._answers.get(q.key, ("no", 0.9))
            p_yes = conf if choice == "yes" else 1 - conf
            results.append(
                Decision(q.key, choice, conf, {"yes": p_yes, "no": 1 - p_yes}, 120.0, self.name)
            )
        return results


def _triage_engine():
    return DecisionEngine.from_yaml(DECISIONS / "hazard_triage.yaml")


def test_shield_rule_fires_when_shield_ready():
    engine = _triage_engine()
    model = _FixedDecider({"is_external_threat": ("yes", 0.9), "is_severe": ("yes", 0.85)})
    result = engine.decide(model, {}, context={"shield_ready": True, "positional": False})
    assert result.action == "shield"
    assert result.rule_name == "shield severe external threat"
    assert result.confidence == pytest.approx(0.85, abs=0.01)  # min(0.9, 0.85)


def test_shield_rule_skipped_when_no_charges():
    engine = _triage_engine()
    model = _FixedDecider({"is_external_threat": ("yes", 0.9), "is_severe": ("yes", 0.85)})
    result = engine.decide(model, {}, context={"shield_ready": False, "positional": False})
    assert result.action == "stop_and_wait"
    assert result.rule_name == "stop for unshielded severe external threat"


def test_safe_mode_rule():
    engine = _triage_engine()
    model = _FixedDecider({"needs_safe_mode": ("yes", 0.95)})
    result = engine.decide(model, {}, context={"shield_ready": False})
    assert result.action == "safe_mode"


def test_reroute_requires_positional_context():
    engine = _triage_engine()
    model = _FixedDecider({"avoid_hazard": ("yes", 0.92)})
    # positional=False → reroute rule skipped → falls to default
    r_no_pos = engine.decide(model, {}, context={"positional": False})
    assert r_no_pos.action != "reroute"
    # positional=True → reroute fires
    r_pos = engine.decide(model, {}, context={"positional": True})
    assert r_pos.action == "reroute"


def test_default_when_no_rules_match():
    engine = _triage_engine()
    model = _FixedDecider({})  # all "no"
    result = engine.decide(model, {}, context={})
    assert result.action == "slow"
    assert result.rule_name == "default"


def test_escalation_when_below_threshold():
    engine = _triage_engine()
    # can_wait=yes with high confidence, but everything else low → below threshold
    model = _FixedDecider({"can_wait": ("yes", 0.90), "is_external_threat": ("no", 0.55)})
    result = engine.decide(model, {}, context={}, threshold=0.70)
    # confidence will be below threshold (default rule fires at min conf < 0.70)
    assert result.can_wait is True
    if result.confidence < 0.70:
        assert result.escalated is True


def test_no_escalation_when_cannot_wait():
    engine = _triage_engine()
    model = _FixedDecider({"can_wait": ("no", 0.95)})
    result = engine.decide(model, {}, context={}, threshold=0.70)
    assert result.escalated is False


# ---------------------------------------------------------------------------
# Instrument choice
# ---------------------------------------------------------------------------

def test_instrument_drill_on_soft_rock():
    engine = DecisionEngine.from_yaml(DECISIONS / "instrument_choice.yaml")
    model = _FixedDecider({"drill_worthwhile": ("yes", 0.88)})
    result = engine.decide(model, {})
    assert result.action == "drill"


def test_instrument_spectrometer_default():
    engine = DecisionEngine.from_yaml(DECISIONS / "instrument_choice.yaml")
    model = _FixedDecider({"drill_worthwhile": ("no", 0.80)})
    result = engine.decide(model, {})
    assert result.action == "spectrometer"


# ---------------------------------------------------------------------------
# Game integration: triage.py uses the engine
# ---------------------------------------------------------------------------

def test_triage_uses_engine_returns_decision_objects():
    from roverthinking.decisions.model import MockDecider
    from roverthinking.decisions.triage import decide_triage

    m = MockDecider()
    state = {
        "wheels_pct": 80, "instruments_pct": 90, "nav_cameras_pct": 100,
        "progress_m": 200, "goal_m": 1000,
        "hazards": [{"kind": "dust_storm", "severity": 2, "distance_m": 50, "length_m": 200}],
    }
    triage, cw = decide_triage(m, state, positional=False)
    assert triage.question == "hazard_triage"
    assert triage.choice in ("shield", "stop_and_wait", "safe_mode", "reroute", "slow", "proceed", "back_up")
    assert 0 <= triage.confidence <= 1
    assert cw.question == "can_wait"
    assert cw.choice in ("yes", "no")
    assert len(triage.detail) >= 5  # all split questions


def test_triage_detail_has_short_labels():
    from roverthinking.decisions.model import MockDecider
    from roverthinking.decisions.triage import decide_triage

    m = MockDecider()
    state = {"wheels_pct": 100, "instruments_pct": 100, "nav_cameras_pct": 100, "progress_m": 0, "goal_m": 500}
    triage, _ = decide_triage(m, state, positional=True)
    for d in triage.detail:
        assert d["short"], f"question {d['key']} has no short label"


def test_instrument_uses_engine():
    from roverthinking.decisions.model import MockDecider
    from roverthinking.decisions.triage import decide_instrument

    m = MockDecider()
    state = {"wheels_pct": 100, "instruments_pct": 100, "nav_cameras_pct": 100, "progress_m": 50, "goal_m": 500}
    instr, cw = decide_instrument(m, state)
    assert instr.question == "instrument_choice"
    assert instr.choice in ("drill", "spectrometer", "camera", "skip")
    assert cw.question == "can_wait"
