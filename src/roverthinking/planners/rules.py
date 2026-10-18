"""Deterministic baseline planner: fixed rules, no models, no context."""

from __future__ import annotations

from roverthinking.decisions.questions import HAZARD_TRIAGE, INSTRUMENT_CHOICE
from roverthinking.planners.base import (
    emit_action,
    execute_science,
    execute_triage,
    log_decision,
    next_ref,
    summarize_action,
)
from roverthinking.sim.actions import ActionResult
from roverthinking.sim.rover import Alarm, Rover


def triage_rule(a: Alarm, shield_charges: int = 0) -> str:
    """Fixed choices by hazard and severity; ignores everything else about the rover."""
    if a.reason == "stuck":
        return "back_up"
    if a.reason == "stalled":
        return "reroute"
    sev = a.severity
    shield = "shield" if shield_charges > 0 else "stop_and_wait"
    return {
        "dust_storm": shield if sev == 3 else ("stop_and_wait" if sev == 2 else "slow"),
        "rockfall": "slow" if sev == 1 else "reroute",
        "sand_trap": "slow" if sev == 1 else "reroute",
        "steep_slope": "slow" if sev == 1 else "reroute",
        "sensor_fault": "slow" if sev == 1 else "safe_mode",
        "solar_flare": shield if sev >= 2 else "stop_and_wait",
        "meteor_shower": shield if sev >= 2 else "slow",
    }.get(a.kind, "slow")


class RulesPlanner:
    name = "rules"

    def step(self, r: Rover) -> list[ActionResult]:
        out: list[ActionResult] = []
        for a in r.pending_alarms():
            h = r.hazard(a.hazard_id)
            if h is None or h.resolved:
                a.state = "resolved"
                continue
            ref = next_ref(r)
            if a.kind == "strange_rock":
                key, choice, opts = "instrument_choice", "spectrometer", INSTRUMENT_CHOICE.options
                rule = "fixed table: always use the spectrometer"
            else:
                key, opts = "hazard_triage", HAZARD_TRIAGE.options
                choice = triage_rule(a, r.shield_charges)
                rule = f"fixed table: {a.kind.replace('_', ' ')}, severity {a.severity} -> {choice}"
            log_decision(
                r,
                self.name,
                a,
                key,
                choice,
                1.0,
                options=list(opts),
                ref=ref,
                parts=[],
                rule=rule,
                state=r.snapshot(h),
            )
            results = (
                execute_science(r, a, choice) if key == "instrument_choice" else execute_triage(r, a, choice)
            )
            emit_action(r, ref, summarize_action(r, a, key, choice, results), results)
            out += results
        return out
