"""The rover, its world and the tick loop. No UI imports.

One tick is one simulated minute-ish of rover time (a sol is TICKS_PER_SOL ticks). The rover
drives east along a traverse toward a goal. Hazards appear ahead of it (or on it); something
must decide how to respond before the rover reaches them. Everything random goes through
`Rover.rng`, so a seed reproduces a run exactly. Anything the screen might animate is appended
to `Rover.log` as a `SimEvent`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

TICKS_PER_SOL = 1480
GOAL_M = 2000.0
DETECT_RANGE = 130.0  # metres: how far ahead the rover sees a hazard
SPEED = {"normal": 2.0, "slow": 0.7}  # metres per tick
DETOUR_SPEED = 0.8
START_HOUR = 9.0
STALL_TICKS = 150  # a held rover re-raises the alarm after this long
SHIELD_CHARGES = 2  # uses per mission
SHIELD_DEPLOY = 8  # ticks to raise the shield (stationary and unprotected meanwhile)
SHIELD_RETRACT = 5

TRIAGE_CHOICES = ("proceed", "slow", "reroute", "stop_and_wait", "safe_mode", "shield", "back_up")
INSTRUMENTS = ("camera", "spectrometer", "drill", "skip")


@dataclass
class Hazard:
    id: int
    kind: str
    severity: int  # 1 minor, 2 major, 3 catastrophic
    x: float | None  # where it starts along the traverse; None = affects the rover itself
    length: float
    start_tick: int
    duration: int | None  # timed hazards only
    detected: bool = False
    resolved: bool = False
    response: str | None = None
    awaiting_earth: bool = False
    bypass: bool = False
    plan: str | None = None  # strange_rock: instrument chosen
    rock_type: str = ""
    description: str = ""
    hold_since: int | None = None


@dataclass
class Alarm:
    id: int
    hazard_id: int
    kind: str
    severity: int
    tick: int
    reason: str = "detected"  # detected | stalled | stuck
    state: str = "new"  # new | ack | resolved


@dataclass(frozen=True)
class SimEvent:
    tick: int
    kind: str
    data: dict[str, Any]


@dataclass
class Job:
    kind: str  # sample | recover
    end_tick: int
    hazard_id: int | None = None
    instrument: str | None = None


class Rover:
    def __init__(self, seed: int = 0, random_rate: float = 0.0) -> None:
        self.rng = random.Random(seed)
        self.seed = seed
        self.random_rate = random_rate
        self.tick_n = 0
        self.x = 0.0
        self.mode = "driving"  # driving | held | safe | shielded | stuck | working | dead | done
        self.dead_reason = ""
        self.wheels = 100.0
        self.instruments = 100.0
        self.nav = 100.0
        self.samples = 0
        self.science = 0
        self.cmd_hold = False
        self.cmd_speed: str | None = None
        self.cmd_shield = False
        self.safe = False
        self.safe_until = 0
        self.shield_charges = SHIELD_CHARGES
        self.shield = "off"  # off | deploying | up | retracting
        self.shield_until = 0
        self.stuck = False
        self.stuck_by: int | None = None
        self.job: Job | None = None
        self.velocity = 0.0
        self.visibility = 1.0  # 1 clear .. 0.08 dense dust
        self.flare_active = False
        self.hazards: list[Hazard] = []
        self.alarms: list[Alarm] = []
        self.schedule: list[tuple[int, str, float | None, int | None]] = []
        self.log: list[SimEvent] = []
        self._hid = 1
        self.decision_refs = 0
        self._aid = 1
        self._offset = int(START_HOUR / 24 * TICKS_PER_SOL)
        self.health_sum = 0.0
        self.damage_taken = 0.0

    # ---- views ------------------------------------------------------------------------------

    @property
    def sol(self) -> int:
        return (self.tick_n + self._offset) // TICKS_PER_SOL + 1

    @property
    def hour(self) -> float:
        return ((self.tick_n + self._offset) % TICKS_PER_SOL) / TICKS_PER_SOL * 24

    @property
    def daylight(self) -> float:
        h = self.hour
        return max(0.0, math.sin(math.pi * (h - 6) / 12)) if 6 <= h <= 18 else 0.0

    @property
    def alive(self) -> bool:
        return self.mode != "dead"

    @property
    def finished(self) -> bool:
        return self.mode in ("dead", "done")

    @property
    def shielded(self) -> bool:
        return self.shield == "up"

    @property
    def comms_ok(self) -> bool:
        return not self.flare_active and self.alive

    @property
    def progress(self) -> float:
        return min(1.0, self.x / GOAL_M)

    def emit(self, kind: str, /, **data: Any) -> None:
        self.log.append(SimEvent(self.tick_n, kind, data))

    def events_since(self, cursor: int) -> tuple[list[SimEvent], int]:
        return self.log[cursor:], len(self.log)

    def active_hazards(self) -> list[Hazard]:
        return [h for h in self.hazards if not h.resolved]

    def detected_hazards(self) -> list[Hazard]:
        return [h for h in self.hazards if h.detected and not h.resolved]

    def hazard(self, hid: int) -> Hazard | None:
        return next((h for h in self.hazards if h.id == hid), None)

    def pending_alarms(self) -> list[Alarm]:
        return [a for a in self.alarms if a.state == "new"]

    def eta_ticks(self, h: Hazard) -> float:
        if h.x is None:
            return 0.0
        return max(0.0, (h.x - self.x) / SPEED["normal"])

    def snapshot(self, focus: Hazard | None = None) -> dict[str, Any]:
        """Compact JSON-able state: what Decider and Earth are shown.

        With `focus`, only that hazard is listed (so "this hazard" is unambiguous) and the
        others are counted.
        """
        shown = [focus] if focus is not None else self.detected_hazards()
        others = len([h for h in self.detected_hazards() if h is not focus]) if focus else 0
        return {
            "sol": self.sol,
            "hour": round(self.hour, 1),
            "mode": self.mode,
            "progress_m": round(self.x),
            "goal_m": int(GOAL_M),
            "wheels_pct": round(self.wheels),
            "instruments_pct": round(self.instruments),
            "nav_cameras_pct": round(self.nav),
            "comms_ok": self.comms_ok,
            "stuck": self.stuck,
            "safe_mode": self.safe,
            "shield": {"charges": self.shield_charges, "of": SHIELD_CHARGES, "state": self.shield},
            "samples": self.samples,
            "hazards": [
                {
                    "id": h.id,
                    "kind": h.kind,
                    "severity": h.severity,
                    "distance_m": None if h.x is None else round(h.x - self.x),
                    "length_m": round(h.length),
                    "response": h.response,
                    **({"description": h.description} if h.description else {}),
                }
                for h in shown
            ],
            **({"other_hazards": others} if others else {}),
        }

    # ---- scheduling and editing events --------------------------------------------------------

    def schedule_hazard(
        self, at_tick: int, kind: str, ahead: float | None = None, severity: int | None = None
    ) -> None:
        self.schedule.append((at_tick, kind, ahead, severity))
        self.schedule.sort(key=lambda s: s[0])

    def start_hazard(self, kind: str, ahead: float | None = None, severity: int | None = None):
        from roverthinking.sim.hazards import spawn

        return spawn(self, kind, ahead, severity)

    def remove_hazard(self, hid: int) -> bool:
        """Take an active hazard away (a player edit). Frees the rover if it caused a jam."""
        from roverthinking.sim.hazards import resolve

        h = self.hazard(hid)
        if h is None or h.resolved:
            return False
        resolve(self, h, "removed")
        if self.stuck_by == h.id:
            self.stuck, self.stuck_by = False, None
            self.emit("freed")
        return True

    def cancel_scheduled(self, index: int) -> bool:
        if 0 <= index < len(self.schedule):
            self.schedule.pop(index)
            return True
        return False

    # ---- the tick -----------------------------------------------------------------------------------

    def tick(self) -> None:
        from roverthinking.sim import hazards as hz

        if self.finished:
            return
        self.tick_n += 1
        while self.schedule and self.schedule[0][0] <= self.tick_n:
            _, kind, ahead, sev = self.schedule.pop(0)
            self.start_hazard(kind, ahead, sev)
        if self.random_rate and self.rng.random() < self.random_rate:
            self.start_hazard(self.rng.choice(hz.HAZARD_KINDS))

        self.visibility, self.flare_active = 1.0, False
        for h in self.active_hazards():
            hz.apply(self, h)
        self._update_shield()
        v = self._control()
        self._move(v)
        self._jobs()
        self._check_end()
        self.health_sum += self.health()

    def health(self) -> float:
        return 0.55 * self.wheels + 0.30 * self.instruments + 0.15 * self.nav

    # ---- internals --------------------------------------------------------------------------------

    def _responses(self) -> set[str]:
        return {h.response for h in self.detected_hazards() if h.response}

    def _update_shield(self) -> None:
        s = self.shield
        if s == "deploying" and self.tick_n >= self.shield_until:
            self.shield = "up"
            self.emit("shield_up")
        elif s == "up":
            needed = any(h.response == "shield" for h in self.detected_hazards())
            if not needed and not self.cmd_shield:
                self.shield, self.shield_until = "retracting", self.tick_n + SHIELD_RETRACT
                self.emit("shield_retract")
        elif s == "retracting" and self.tick_n >= self.shield_until:
            self.shield = "off"
            self.emit("shield_down")
            for h in self.detected_hazards():
                if h.response == "shield":
                    h.response = None

    def _control(self) -> float:
        """Work out this tick's speed in metres per tick (0 when not moving)."""
        resp = self._responses()
        if self.safe and self.tick_n >= self.safe_until and not self._needs_safe():
            self.safe = False
            self.emit("safe_mode_exit")
            for h in self.detected_hazards():
                if h.response == "safe_mode":
                    h.response = None
        in_zone_bypass = any(self._in_zone(h) and h.bypass for h in self.active_hazards())
        if self.shield != "off":
            mode, v = "shielded", 0.0
        elif self.safe:
            mode, v = "safe", 0.0
        elif self.stuck:
            mode, v = "stuck", 0.0
        elif self.job is not None:
            mode, v = "working", 0.0
        elif self.cmd_hold or "stop_and_wait" in resp:
            mode, v = "held", 0.0
        else:
            slow = self.cmd_speed == "slow" or "slow" in resp or self.nav < 40
            v = SPEED["slow"] if slow else SPEED["normal"]
            if in_zone_bypass:
                v = DETOUR_SPEED
            mode = "driving"
        if mode != self.mode:
            self.emit("mode", mode=mode, was=self.mode)
        self.mode = mode
        self._stall_check()
        return v

    def _needs_safe(self) -> bool:
        return any(h.kind == "solar_flare" for h in self.active_hazards())

    def _in_zone(self, h: Hazard) -> bool:
        return h.x is not None and h.x <= self.x < h.x + max(h.length, 1.0)

    def _stall_check(self) -> None:
        for h in self.detected_hazards():
            if h.x is None or h.awaiting_earth or h.response != "stop_and_wait":
                h.hold_since = None
                continue
            if h.hold_since is None:
                h.hold_since = self.tick_n
            elif self.tick_n - h.hold_since >= STALL_TICKS:
                h.hold_since = self.tick_n
                from roverthinking.sim.hazards import raise_alarm

                raise_alarm(self, h, "stalled")

    def _move(self, v: float) -> None:
        from roverthinking.sim import hazards as hz

        self.velocity = v
        if v > 0:
            self.x += v
        for h in self.active_hazards():
            if h.x is not None:
                hz.zone_tick(self, h, v)

    def _jobs(self) -> None:
        j = self.job
        if j is None or self.tick_n < j.end_tick:
            return
        self.job = None
        if j.kind == "sample":
            from roverthinking.sim.hazards import finish_sample

            finish_sample(self, j)
        elif j.kind == "recover":
            if self.rng.random() < 0.7:
                self.stuck, self.stuck_by = False, None
                self.emit("freed")
            else:
                self.emit("recover_failed")
                from roverthinking.sim.hazards import raise_alarm

                h = self.hazard(j.hazard_id) if j.hazard_id is not None else None
                if h is not None and not h.resolved:
                    raise_alarm(self, h, "stuck")

    def _check_end(self) -> None:
        if self.wheels <= 0 and self.alive:
            self._die("wheels failed")
        elif self.x >= GOAL_M and self.alive and self.mode != "done":
            self.mode = "done"
            self.emit("goal_reached", x=round(self.x))

    def damage(self, what: str, amount: float, cause: str) -> None:
        if self.shielded:  # nothing gets through the shield, whatever its source
            return
        cur = getattr(self, what)
        setattr(self, what, max(0.0, cur - amount))
        self.damage_taken += amount
        self.emit("damage", part=what, amount=round(amount, 1), cause=cause)

    def _die(self, reason: str) -> None:
        if self.mode != "dead":
            self.mode = "dead"
            self.dead_reason = reason
            self.velocity = 0.0
            self.emit("rover_dead", reason=reason)
