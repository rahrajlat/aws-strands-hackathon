"""The Steward: Decider 2B decides first; Earth is asked only when it is worth the wait.

Per alarm: Decider's narrow questions give a choice and a confidence.
  confidence >= threshold          -> act
  below, and waiting is affordable -> stop and ask Earth (the rover holds until the reply)
  below, and waiting is not        -> act on the best local choice anyway ("acted under
                                      uncertainty")
Orders and Earth replies are vetted against the *current* state (Stale Order Check) before
they run. Model calls never block: they run on `ctx.executor`, results apply on the sim thread
after the model's latency has been charged in sim time.

Every decision leaves a trail of events the screen shows: `decision_pending` (what the model was
shown and asked), `decision` (its answers and the choice) and `decision_action` (what the rover
then did).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from roverthinking.decisions import questions as Q
from roverthinking.decisions.model import Decision
from roverthinking.decisions.render import as_prose
from roverthinking.decisions.triage import SPLIT, decide_instrument, decide_triage
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
from roverthinking.planners.rules import triage_rule
from roverthinking.sim import actions
from roverthinking.sim.actions import ActionResult
from roverthinking.sim.rover import Alarm, Hazard, Rover

STATIC = ("rockfall", "sand_trap", "steep_slope", "strange_rock")


@dataclass
class _Job:
    submit_tick: int
    future: Future | _Done
    finish: Callable[[Any], None]
    on_error: Callable[[], None] | None = None


class _Done:
    """A finished result (or failure) for models run inline instead of in a thread."""

    def __init__(self, value: Any = None, error: Exception | None = None) -> None:
        self._v, self._e = value, error

    def done(self) -> bool:
        return True

    def result(self) -> Any:
        if self._e is not None:
            raise self._e
        return self._v


@dataclass
class _Pending:
    alarm_id: int
    choice: str
    question: str
    deadline: int
    ref: int


@dataclass
class Steward:
    ctx: PlannerContext
    name: str = "steward"
    _jobs: list[_Job] = field(default_factory=list)
    _pending: dict[int, _Pending] = field(default_factory=dict)

    # ---- the per-tick entry point -----------------------------------------------------------

    def step(self, r: Rover) -> list[ActionResult]:
        out: list[ActionResult] = []
        for a in r.pending_alarms():
            h = r.hazard(a.hazard_id)
            if h is None or h.resolved:
                a.state = "resolved"
                continue
            a.state = "ack"
            out += self._handle(r, a, h)
        out += self._collect(r)
        out += self._timeouts(r)
        return out

    # ---- alarms -----------------------------------------------------------------------------------

    def _handle(self, r: Rover, a: Alarm, h: Hazard) -> list[ActionResult]:
        if a.reason in ("stuck", "stalled"):
            return self._reflex(r, a, h)
        snap = r.snapshot(h)
        ref = next_ref(r)
        if a.kind == "strange_rock":
            qs = [Q.DRILL_WORTHWHILE, Q.CAN_WAIT]
            emit_pending(r, self.name, ref, "instrument_choice", a, as_prose(snap), qs)
            self._submit(
                r,
                lambda: decide_instrument(self.ctx.decider, snap),
                lambda res: self._decided(r, a, ref, "instrument_choice", Q.INSTRUMENT_CHOICE, snap, *res),
                lambda: self._fallback(r, a, ref),
            )
        else:
            qs = [*SPLIT, Q.CAN_WAIT]
            positional = h.x is not None
            emit_pending(r, self.name, ref, "hazard_triage", a, as_prose(snap), qs)
            self._submit(
                r,
                lambda: decide_triage(self.ctx.decider, snap, positional),
                lambda res: self._decided(r, a, ref, "hazard_triage", Q.HAZARD_TRIAGE, snap, *res),
                lambda: self._fallback(r, a, ref),
            )
        return []

    def _reflex(self, r: Rover, a: Alarm, h: Hazard) -> list[ActionResult]:
        """Cases with one obvious answer need no model: free yourself; reroute when held."""
        if a.reason == "stuck":
            choice, why = "back_up", "the rover is stuck, so it must reverse out"
        elif h.kind in ("rockfall", "sand_trap", "steep_slope") and h.x and r.x < h.x:
            choice, why = "reroute", "held too long in front of a fixed hazard, so go around it"
        else:
            choice, why = "slow", "held too long, so creep forward"
        ref = next_ref(r)
        log_decision(
            r,
            self.name,
            a,
            "hazard_triage",
            choice,
            1.0,
            source="reflex",
            ref=ref,
            parts=[],
            rule=f"reflex: {why}",
            options=list(Q.HAZARD_TRIAGE.options),
            state=r.snapshot(h),
        )
        return self._act(r, a, "hazard_triage", choice, ref)

    def _decided(
        self, r: Rover, a: Alarm, ref: int, key: str, q, snap, decision: Decision, can_wait: Decision
    ) -> list[ActionResult]:
        h = r.hazard(a.hazard_id)
        conf, choice = decision.confidence, decision.choice
        extra = {
            "options": list(q.options),
            "source": decision.source,
            "state": snap,
            "ref": ref,
            "parts": list(decision.detail),
            "prose": as_prose(snap),
        }
        if h is None or h.resolved:
            log_decision(
                r, self.name, a, key, choice, conf, latency_ms=decision.latency_ms, late=True, **extra
            )
            emit_action(r, ref, "The hazard was already behind the rover; nothing to do", [])
            return []
        if conf >= self.ctx.threshold:
            log_decision(r, self.name, a, key, choice, conf, latency_ms=decision.latency_ms, **extra)
            return self._act(r, a, key, choice, ref)
        waiting_ok = (
            self.ctx.escalate is not None
            and r.comms_ok
            and not r.stuck
            and h.kind in STATIC
            and can_wait.choice == "yes"
        )
        if waiting_ok:
            log_decision(
                r, self.name, a, key, choice, conf, escalated=True, latency_ms=decision.latency_ms, **extra
            )
            res = actions.hold_for_earth(r, h.id)
            self._pending[h.id] = _Pending(a.id, choice, key, r.tick_n + self.ctx.reply_timeout_ticks, ref)
            self.ctx.escalate(
                f"{h.kind.replace('_', ' ')} sev {h.severity}: unsure between local choice "
                f"'{choice}' ({conf:.2f})",
                {
                    "hazard_id": h.id,
                    "question": key,
                    "options": dict(q.options),
                    "local_choice": choice,
                    "local_confidence": conf,
                    "snapshot": r.snapshot(h),
                    "ref": ref,
                },
            )
            emit_action(
                r,
                ref,
                f"Not sure enough ({conf:.2f} < {self.ctx.threshold:.2f}); stopped and asked Earth",
                [res],
            )
            return []
        log_decision(
            r,
            self.name,
            a,
            key,
            choice,
            conf,
            latency_ms=decision.latency_ms,
            under_uncertainty=True,
            **extra,
        )
        return self._act(r, a, key, choice, ref, note="acting on the best guess; cannot afford to wait")

    def _act(
        self, r: Rover, a: Alarm, key: str, choice: str, ref: int | None, note: str = ""
    ) -> list[ActionResult]:
        results = (
            execute_science(r, a, choice) if key == "instrument_choice" else execute_triage(r, a, choice)
        )
        summary = summarize_action(r, a, key, choice, results)
        emit_action(r, ref, f"{summary} ({note})" if note else summary, results)
        return results

    # ---- Earth's replies ------------------------------------------------------------------------

    def on_reply(self, r: Rover, payload: dict[str, Any]) -> None:
        hid = payload["hazard_id"]
        h, pend = r.hazard(hid), self._pending.pop(hid, None)
        if pend is None:
            return
        rec = payload["recommendation"]
        reasoning = payload.get("reasoning", "")
        a = next((x for x in r.alarms if x.id == pend.alarm_id), None)
        if h is None or h.resolved or a is None:
            r.emit("reply_stale", hazard_id=hid, reason="the hazard is already behind the rover")
            emit_action(
                r, pend.ref, "Earth's reply came too late: the hazard is already behind the rover", []
            )
            return
        snap = {**r.snapshot(h), "order": f"Earth recommends: {rec}"}
        ref = next_ref(r)
        emit_pending(r, self.name, ref, "reply_check", a, as_prose(snap), [Q.ORDER_STILL_VALID])

        def finish(res: Decision) -> None:
            blocked = res.choice == "no" and res.confidence >= self.ctx.stale_block_conf
            parts = [
                {
                    "key": Q.ORDER_STILL_VALID.key,
                    "short": "Earth's advice still valid?",
                    "text": Q.ORDER_STILL_VALID.text,
                    "answer": res.choice,
                    "confidence": round(res.confidence, 3),
                }
            ]
            r.emit(
                "decision",
                planner=self.name,
                question="reply_check",
                choice="hold" if blocked else "apply",
                confidence=round(res.confidence, 3),
                alarm=a.id,
                hazard=a.kind,
                hazard_id=hid,
                severity=a.severity,
                reason="reply",
                escalated=False,
                latency_ms=round(res.latency_ms, 1),
                ideal=None,
                options=["apply", "hold"],
                state=snap,
                ref=ref,
                parts=parts,
                prose=as_prose(snap),
                reasoning=reasoning,
                source=res.source,
            )
            if blocked:
                reason = _stale_reason(r, h)
                r.emit("reply_stale", hazard_id=hid, reason=reason)
                emit_action(r, ref, f"Earth's advice is stale ({reason}); using the local choice instead", [])
                self._act(r, a, pend.question, pend.choice, None)
                return
            log_decision(
                r,
                "earth",
                a,
                pend.question,
                rec,
                1.0,
                source="earth",
                ref=ref,
                parts=[],
                options=payload.get("options"),
                reasoning=reasoning,
                state=snap,
                rule=f"Earth advised: {reasoning}" if reasoning else "Earth advised",
            )
            self._act(r, a, pend.question, rec, ref, note="Earth's advice")

        def apply_unvetted() -> None:
            self._act(r, a, pend.question, rec, ref, note="Earth's advice, unchecked: decider offline")

        self._submit(r, lambda: self.ctx.decider.decide(snap, Q.ORDER_STILL_VALID), finish, apply_unvetted)

    def vet_order(
        self, r: Rover, text: str, action: str, execute: Callable[[], None], hold: Callable[[str], None]
    ) -> None:
        """Stale Order Check: is this order from Earth still valid and safe right now?"""
        snap = {**r.snapshot(), "order": text}
        ref = next_ref(r)
        emit_pending(r, self.name, ref, "order_valid", None, as_prose(snap), [Q.ORDER_STILL_VALID])

        def finish(res: Decision) -> None:
            blocked = res.choice == "no" and res.confidence >= self.ctx.stale_block_conf
            parts = [
                {
                    "key": Q.ORDER_STILL_VALID.key,
                    "short": Q.ORDER_STILL_VALID.short,
                    "text": Q.ORDER_STILL_VALID.text,
                    "answer": res.choice,
                    "confidence": round(res.confidence, 3),
                }
            ]
            r.emit(
                "decision",
                planner=self.name,
                question="order_valid",
                choice="hold" if blocked else "run",
                confidence=round(res.confidence, 3),
                alarm=None,
                hazard="order",
                hazard_id=None,
                severity=0,
                reason="order",
                escalated=False,
                latency_ms=round(res.latency_ms, 1),
                ideal=None,
                options=["run", "hold"],
                order=text,
                state=snap,
                ref=ref,
                parts=parts,
                prose=as_prose(snap),
                source=res.source,
            )
            if blocked:
                reason = _stale_reason(r, None)
                emit_action(r, ref, f"Order held: {reason}", [])
                hold(reason)
            else:
                emit_action(r, ref, f"Running the order '{text}'", [])
                execute()

        def run_anyway() -> None:
            emit_action(r, ref, f"Decider offline: running the order '{text}' unchecked", [])
            execute()

        self._submit(r, lambda: self.ctx.decider.decide(snap, Q.ORDER_STILL_VALID), finish, run_anyway)

    # ---- when the model is unavailable ----------------------------------------------------------

    def _fallback(self, r: Rover, a: Alarm, ref: int | None) -> None:
        """The decider failed: use the fixed rules table for this one decision."""
        h = r.hazard(a.hazard_id)
        if h is None or h.resolved:
            return
        if a.kind == "strange_rock":
            key, choice = "instrument_choice", "spectrometer"
        else:
            key, choice = "hazard_triage", triage_rule(a, r.shield_charges)
        log_decision(
            r,
            self.name,
            a,
            key,
            choice,
            0.0,
            under_uncertainty=True,
            source="fallback-rules",
            options=[],
            ref=ref,
            parts=[],
            rule="decider unavailable: used the fixed rules table",
            state=r.snapshot(h),
        )
        self._act(r, a, key, choice, ref, note="decider offline: rules table")

    # ---- plumbing -----------------------------------------------------------------------------------

    def _submit(
        self,
        r: Rover,
        work: Callable[[], Any],
        finish: Callable[[Any], None],
        on_error: Callable[[], None] | None = None,
    ) -> None:
        ex = self.ctx.executor
        if ex is not None:
            fut: Future | _Done = ex.submit(work)
        else:
            try:
                fut = _Done(work())
            except Exception as exc:  # noqa: BLE001 - any model failure degrades gracefully
                fut = _Done(error=exc)
        self._jobs.append(_Job(r.tick_n, fut, finish, on_error))

    def _collect(self, r: Rover) -> list[ActionResult]:
        for job in list(self._jobs):
            if not job.future.done():
                continue
            try:
                res = job.future.result()
            except Exception as exc:  # noqa: BLE001
                self._jobs.remove(job)
                r.emit("decider_error", error=str(exc)[:90])
                if job.on_error is not None:
                    job.on_error()
                continue
            latency = _latency_of(res)
            if r.tick_n < job.submit_tick + math.ceil(latency / self.ctx.ms_per_tick):
                continue  # the model's thinking time is charged in sim time
            self._jobs.remove(job)
            job.finish(res)
        return []

    def _timeouts(self, r: Rover) -> list[ActionResult]:
        out: list[ActionResult] = []
        for hid, p in list(self._pending.items()):
            if r.tick_n >= p.deadline:
                del self._pending[hid]
                h = r.hazard(hid)
                a = next((x for x in r.alarms if x.id == p.alarm_id), None)
                r.emit("reply_timeout", hazard_id=hid)
                if h and not h.resolved and a:
                    out += self._act(r, a, p.question, p.choice, p.ref, note="no reply from Earth in time")
        return out


def _latency_of(res: Any) -> float:
    if isinstance(res, Decision):
        return res.latency_ms
    if isinstance(res, tuple):
        return max((x.latency_ms for x in res if isinstance(x, Decision)), default=0.0)
    return 0.0


def _stale_reason(r: Rover, h: Hazard | None) -> str:
    if r.stuck:
        return "the rover is now stuck"
    if r.safe:
        return "the rover is in safe mode"
    worst = max(
        (x for x in r.detected_hazards() if x.kind != "strange_rock"),
        key=lambda x: x.severity,
        default=None,
    )
    if worst is not None:
        sev = {1: "minor", 2: "major", 3: "catastrophic"}[worst.severity]
        where = "" if worst.x is None else f" {max(0, round(worst.x - r.x))} m ahead"
        return f"a {sev} {worst.kind.replace('_', ' ')}{where} appeared since it was sent"
    return "conditions changed while it was in flight"
