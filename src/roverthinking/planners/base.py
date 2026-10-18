"""Planner interface and the shared executors that turn a choice into actions.

Every planner (rules, llm_only, steward) picks a triage option or an instrument; this module
runs it the same way for all of them, so comparisons between planners are fair.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, Protocol

from roverthinking.sim import actions, oracle
from roverthinking.sim.actions import ActionResult
from roverthinking.sim.rover import Alarm, Rover


class Planner(Protocol):
    name: str

    def step(self, rover: Rover) -> list[ActionResult]:
        """Called once per tick, after the rover has ticked."""
        ...


@dataclass
class PlannerContext:
    """Everything a model-using planner needs from the outside world."""

    decider: Any = None  # DecisionModel
    threshold: float = 0.70
    executor: Executor | None = None  # None = run models inline (benchmark, tests)
    ms_per_tick: float = 100.0  # how much model latency costs in sim time
    reply_timeout_ticks: int = 600
    escalate: Callable[[str, dict[str, Any]], None] | None = None  # sends a message to Earth
    stale_block_conf: float = 0.6


def execute_triage(r: Rover, alarm: Alarm, choice: str) -> list[ActionResult]:
    return [actions.respond(r, alarm.hazard_id, choice)]


def execute_science(r: Rover, alarm: Alarm, instrument: str) -> list[ActionResult]:
    return [actions.sample(r, alarm.hazard_id, instrument)]


def ideal_for(r: Rover, alarm: Alarm) -> str | None:
    h = r.hazard(alarm.hazard_id)
    if h is None:
        return None
    return oracle.ideal_instrument(h) if h.kind == "strange_rock" else oracle.ideal_triage(r, h)


HAZARD_NAME = {
    "dust_storm": "dust storm",
    "rockfall": "boulder field",
    "sand_trap": "sand trap",
    "steep_slope": "steep slope",
    "sensor_fault": "sensor fault",
    "solar_flare": "solar flare",
    "meteor_shower": "meteor shower",
    "strange_rock": "rock",
}
ACTION_TEXT = {
    "proceed": "Carrying on at normal speed",
    "slow": "Crawling through at low speed",
    "reroute": "Steering around the {hz}",
    "stop_and_wait": "Stopping to wait out the {hz}",
    "safe_mode": "Entering safe mode and rebooting",
    "shield": "Raising the shield against the {hz}",
    "back_up": "Reversing to free the wheels",
}


def next_ref(r: Rover) -> int:
    r.decision_refs += 1
    return r.decision_refs


def summarize_action(
    r: Rover, alarm: Alarm | None, key: str, choice: str, results: list[ActionResult]
) -> str:
    """One plain sentence for what the rover actually did (or why it could not)."""
    hz = HAZARD_NAME.get(alarm.kind, alarm.kind) if alarm else "situation"
    bad = [x for x in results if not x.ok]
    if bad:
        return f"✗ could not {choice.replace('_', ' ')}: {bad[0].reason}"
    if key == "instrument_choice":
        return "Skipping the rock" if choice == "skip" else f"Examining the rock with the {choice}"
    return ACTION_TEXT.get(choice, choice).format(hz=hz)


def emit_pending(
    r: Rover, planner: str, ref: int, key: str, alarm: Alarm | None, prose: str, questions: list
) -> None:
    """Announce that a model has been asked, and exactly what it was shown."""
    r.emit(
        "decision_pending",
        ref=ref,
        planner=planner,
        question=key,
        hazard=alarm.kind if alarm else "order",
        hazard_id=alarm.hazard_id if alarm else None,
        severity=alarm.severity if alarm else 0,
        prose=prose,
        questions=[{"key": q.key, "short": q.short or q.key, "text": q.text} for q in questions],
    )


def emit_action(r: Rover, ref: int | None, summary: str, results: list[ActionResult]) -> None:
    r.emit(
        "decision_action",
        ref=ref,
        summary=summary,
        ok=all(x.ok for x in results) if results else True,
    )


def log_decision(
    r: Rover,
    planner: str,
    alarm: Alarm,
    question: str,
    choice: str,
    confidence: float,
    *,
    escalated: bool = False,
    latency_ms: float = 0.0,
    **extra: Any,
) -> None:
    r.emit(
        "decision",
        planner=planner,
        question=question,
        choice=choice,
        confidence=round(confidence, 3),
        alarm=alarm.id,
        hazard=alarm.kind,
        hazard_id=alarm.hazard_id,
        severity=alarm.severity,
        reason=alarm.reason,
        escalated=escalated,
        latency_ms=round(latency_ms, 1),
        ideal=ideal_for(r, alarm),
        **extra,
    )
