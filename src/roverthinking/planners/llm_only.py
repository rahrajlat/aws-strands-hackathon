"""The comparison planner: every decision goes to Earth through the light delay.

No onboard model. When an alarm arrives the rover stops and asks Earth; it applies the reply when
it arrives, unchecked. Slow, and nothing guards a reply that has gone stale, but it is the "just
use the big model" baseline the Steward is measured against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from roverthinking.decisions import questions as Q
from roverthinking.decisions.render import as_prose
from roverthinking.planners.base import (
    PlannerContext,
    emit_action,
    emit_pending,
    execute_science,
    execute_triage,
    log_decision,
    next_ref,
    summarize_action,
)
from roverthinking.sim import actions
from roverthinking.sim.actions import ActionResult
from roverthinking.sim.rover import Alarm, Hazard, Rover


@dataclass
class _Wait:
    alarm_id: int
    ref: int
    key: str
    deadline: int
    asked: bool = False


@dataclass
class LlmOnlyPlanner:
    ctx: PlannerContext
    name: str = "llm_only"
    _waiting: dict[int, _Wait] = field(default_factory=dict)

    def step(self, r: Rover) -> list[ActionResult]:
        for a in r.pending_alarms():
            h = r.hazard(a.hazard_id)
            if h is None or h.resolved:
                a.state = "resolved"
                continue
            a.state = "ack"
            key = "instrument_choice" if a.kind == "strange_rock" else "hazard_triage"
            ref = next_ref(r)
            emit_pending(r, self.name, ref, key, a, as_prose(r.snapshot(h)), [])
            h.response = "stop_and_wait"  # stand still until Earth answers
            h.awaiting_earth = True
            self._waiting[h.id] = _Wait(a.id, ref, key, r.tick_n + self.ctx.reply_timeout_ticks)
        for hid, w in list(self._waiting.items()):
            h = r.hazard(hid)
            a = next((x for x in r.alarms if x.id == w.alarm_id), None)
            if h is None or h.resolved or a is None:
                del self._waiting[hid]
                continue
            if not w.asked and r.comms_ok:
                self._ask(r, a, h, w)
            elif r.tick_n >= w.deadline:
                del self._waiting[hid]
                self._apply(
                    r,
                    a,
                    w,
                    "spectrometer" if w.key == "instrument_choice" else "stop_and_wait",
                    "",
                    "no reply from Earth in time",
                )
        return []

    def _ask(self, r: Rover, a: Alarm, h: Hazard, w: _Wait) -> None:
        w.asked = True
        q = Q.INSTRUMENT_CHOICE if w.key == "instrument_choice" else Q.HAZARD_TRIAGE
        if self.ctx.escalate is not None:
            self.ctx.escalate(
                f"{h.kind.replace('_', ' ')} sev {h.severity}: what should the rover do?",
                {
                    "hazard_id": h.id,
                    "question": w.key,
                    "options": dict(q.options),
                    "local_choice": "",
                    "local_confidence": 0.0,
                    "snapshot": r.snapshot(h),
                    "ref": w.ref,
                },
            )

    def on_reply(self, r: Rover, payload: dict[str, Any]) -> None:
        w = self._waiting.pop(payload["hazard_id"], None)
        if w is None:
            return
        h = r.hazard(payload["hazard_id"])
        a = next((x for x in r.alarms if x.id == w.alarm_id), None)
        if h is None or h.resolved or a is None:
            r.emit(
                "reply_stale", hazard_id=payload["hazard_id"], reason="the hazard is already behind the rover"
            )
            emit_action(r, w.ref, "Earth's reply came too late: the hazard is already behind the rover", [])
            return
        self._apply(r, a, w, payload["recommendation"], payload.get("reasoning", ""), "")

    def _apply(self, r: Rover, a: Alarm, w: _Wait, choice: str, reasoning: str, note: str) -> None:
        h = r.hazard(a.hazard_id)
        h.awaiting_earth = False
        h.response = None
        q = Q.INSTRUMENT_CHOICE if w.key == "instrument_choice" else Q.HAZARD_TRIAGE
        log_decision(
            r,
            self.name,
            a,
            w.key,
            choice,
            1.0,
            source="earth",
            ref=w.ref,
            parts=[],
            options=list(q.options),
            reasoning=reasoning,
            state=r.snapshot(h),
            rule=note or "every decision goes to Earth; applied as received, unchecked",
        )
        results = (
            execute_science(r, a, choice) if w.key == "instrument_choice" else execute_triage(r, a, choice)
        )
        summary = summarize_action(r, a, w.key, choice, results)
        emit_action(r, w.ref, f"{summary} ({note})" if note else summary, results)
        _ = actions
