"""Hazard triage and instrument choice via the YAML-driven DecisionEngine.

Questions and policy rules live in configs/hazard_triage.yaml and
configs/instrument_choice.yaml. This module is the game's bridge to them.

Public interface (decide_triage, decide_instrument) is unchanged so the
planners that call them need no updates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from decider_framework import DecisionEngine

from roverthinking.decisions import questions as Q
from roverthinking.decisions.model import Decision

_CONFIGS = Path(__file__).parent / "configs"

_triage_engine = DecisionEngine.from_yaml(_CONFIGS / "hazard_triage.yaml")
_instr_engine = DecisionEngine.from_yaml(_CONFIGS / "instrument_choice.yaml")

# Keep SPLIT for steward's emit_pending (same question objects from YAML)
SPLIT = (Q.IS_EXTERNAL_THREAT, Q.IS_SEVERE, Q.NEEDS_SAFE_MODE, Q.AVOID_HAZARD, Q.WAIT_IT_OUT)


def decide_triage(
    model: Any, state: dict[str, Any], positional: bool
) -> tuple[Decision, Decision]:
    """Run hazard-triage via the YAML engine. Returns (triage_decision, can_wait_decision).

    All questions (triage classifiers + can_wait) go in one batched request.
    """
    sh = state.get("shield") or {}
    shield_ready = sh.get("charges", 0) > 0 or sh.get("state") not in (None, "off")
    ctx = {"shield_ready": shield_ready, "positional": positional}

    result = _triage_engine.decide(model, state, context=ctx)

    triage_d = Decision(
        question="hazard_triage",
        choice=result.action,
        confidence=result.confidence,
        probabilities={result.action: result.confidence},
        latency_ms=result.latency_ms,
        source=result.source,
        detail=result.detail,
    )
    can_wait_d = _can_wait_decision(result.can_wait, result.can_wait_confidence, result.latency_ms, result.source)
    return triage_d, can_wait_d


def decide_instrument(
    model: Any, state: dict[str, Any]
) -> tuple[Decision, Decision]:
    """Run instrument-choice via the YAML engine. Returns (instrument_decision, can_wait_decision).

    Both questions go in one batched request.
    """
    result = _instr_engine.decide(model, state)

    instr_d = Decision(
        question="instrument_choice",
        choice=result.action,
        confidence=result.confidence,
        probabilities={result.action: result.confidence},
        latency_ms=result.latency_ms,
        source=result.source,
        detail=result.detail,
    )
    can_wait_d = _can_wait_decision(result.can_wait, result.can_wait_confidence, result.latency_ms, result.source)
    return instr_d, can_wait_d


def _can_wait_decision(yes: bool, conf: float, latency_ms: float, source: str) -> Decision:
    choice = "yes" if yes else "no"
    p_yes = conf if yes else 1 - conf
    return Decision(
        question="can_wait",
        choice=choice,
        confidence=conf,
        probabilities={"yes": p_yes, "no": 1 - p_yes},
        latency_ms=latency_ms,
        source=source,
    )
