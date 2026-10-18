"""Guarded actuators. Every action validates its arguments and rejects with a reason."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from roverthinking.sim.hazards import BYPASSABLE, SAMPLE_TICKS, SHIELDABLE, resolve
from roverthinking.sim.rover import (
    INSTRUMENTS,
    SHIELD_DEPLOY,
    TRIAGE_CHOICES,
    Job,
    Rover,
)

RECOVER_TICKS = 25
SAFE_MODE_TICKS = 30


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    action: str
    reason: str = ""
    detail: dict[str, Any] | None = None


def _reject(r: Rover, action: str, reason: str, **args: Any) -> ActionResult:
    r.emit("action_rejected", action=action, reason=reason, **args)
    return ActionResult(False, action, reason)


def _ok(r: Rover, action: str, **detail: Any) -> ActionResult:
    r.emit("action", action=action, **detail)
    return ActionResult(True, action, detail=detail)


def _alive(r: Rover, action: str) -> ActionResult | None:
    if r.finished:
        return _reject(r, action, f"rover is {r.mode}")
    return None


def respond(r: Rover, hazard_id: int, choice: str) -> ActionResult:
    """Apply a triage choice to a detected hazard."""
    if (err := _alive(r, "respond")) is not None:
        return err
    h = r.hazard(hazard_id)
    if h is None or h.resolved:
        return _reject(r, "respond", f"no active hazard #{hazard_id}", choice=choice)
    if choice not in TRIAGE_CHOICES:
        return _reject(r, "respond", f"unknown response {choice!r}", choice=choice)
    if not h.detected:
        return _reject(r, "respond", "hazard not detected yet", choice=choice)
    if h.kind == "strange_rock":
        return _reject(r, "respond", "a rock needs an instrument choice, not a manoeuvre")
    if choice == "reroute":
        if h.kind not in BYPASSABLE or h.x is None:
            return _reject(r, "respond", f"cannot reroute around {h.kind}", choice=choice)
        if r.x >= h.x:
            return _reject(r, "respond", "already past the point of rerouting", choice=choice)
        h.bypass = True
    if choice == "back_up":
        if not r.stuck:
            return _reject(r, "respond", "rover is not stuck", choice=choice)
        return _start_recover(r, h.id)
    if choice == "shield":
        if h.kind not in SHIELDABLE:
            return _reject(
                r,
                "respond",
                "the shield only helps against dust storms, flares and meteors",
                choice=choice,
            )
        if (err := _raise_shield(r, "respond")) is not None:
            return err
    if choice == "safe_mode":
        _enter_safe(r)
    h.response = choice
    h.awaiting_earth = False
    for a in r.alarms:
        if a.hazard_id == h.id and a.state == "new":
            a.state = "ack"
    return _ok(r, "respond", hazard_id=h.id, hazard=h.kind, choice=choice)


def _raise_shield(r: Rover, action: str) -> ActionResult | None:
    """Start deploying the shield. Returns a rejection, or None on success."""
    if r.shield in ("up", "deploying"):
        return None
    if r.shield == "retracting":
        r.shield = "up"
        return None
    if r.shield_charges <= 0:
        return _reject(r, action, "no shield charges left")
    r.shield_charges -= 1
    r.shield, r.shield_until = "deploying", r.tick_n + SHIELD_DEPLOY
    r.emit("shield_deploy", charges_left=r.shield_charges)
    return None


def _enter_safe(r: Rover) -> None:
    if not r.safe:
        r.safe = True
        r.safe_until = r.tick_n + SAFE_MODE_TICKS
        r.emit("safe_mode_enter")


def _start_recover(r: Rover, hazard_id: int | None = None) -> ActionResult:
    if r.job is not None:
        return _reject(r, "back_up", "already busy")
    r.job = Job("recover", r.tick_n + RECOVER_TICKS, hazard_id)
    for a in r.alarms:
        if a.state == "new" and a.reason == "stuck":
            a.state = "ack"
    return _ok(r, "back_up", eta=RECOVER_TICKS)


def hold_for_earth(r: Rover, hazard_id: int) -> ActionResult:
    """Stop and wait while a message goes to Earth and back."""
    h = r.hazard(hazard_id)
    if h is None or h.resolved:
        return _reject(r, "hold_for_earth", f"no active hazard #{hazard_id}")
    if not r.comms_ok:
        return _reject(r, "hold_for_earth", "no link to Earth")
    h.response = "stop_and_wait"
    h.awaiting_earth = True
    for a in r.alarms:
        if a.hazard_id == h.id and a.state == "new":
            a.state = "ack"
    return _ok(r, "hold_for_earth", hazard_id=h.id, hazard=h.kind)


def sample(r: Rover, hazard_id: int, instrument: str) -> ActionResult:
    if (err := _alive(r, "sample")) is not None:
        return err
    h = r.hazard(hazard_id)
    if h is None or h.resolved or h.kind != "strange_rock":
        return _reject(r, "sample", f"no rock #{hazard_id} to examine")
    if instrument not in INSTRUMENTS:
        return _reject(r, "sample", f"unknown instrument {instrument!r}")
    if instrument != "skip" and r.instruments <= 0:
        return _reject(r, "sample", "instruments are offline")
    h.plan = instrument
    h.awaiting_earth = False
    h.response = None
    for a in r.alarms:
        if a.hazard_id == h.id and a.state == "new":
            a.state = "ack"
    if instrument == "skip":
        resolve(r, h, "skipped")
    return _ok(r, "sample", hazard_id=h.id, instrument=instrument, eta=SAMPLE_TICKS.get(instrument))


# ---- standing orders from Earth (not tied to one hazard) ------------------------------------


def order_stop(r: Rover) -> ActionResult:
    if (err := _alive(r, "stop")) is not None:
        return err
    r.cmd_hold = True
    return _ok(r, "stop")


def order_drive(r: Rover, speed: str = "normal") -> ActionResult:
    if (err := _alive(r, "drive")) is not None:
        return err
    if speed not in ("normal", "slow"):
        return _reject(r, "drive", f"unknown speed {speed!r}")
    if r.safe:
        return _reject(r, "drive", "rover is in safe mode")
    if r.shield != "off":
        return _reject(r, "drive", "the shield is up")
    r.cmd_hold = False
    r.cmd_speed = speed
    return _ok(r, "drive", speed=speed)


def order_safe_mode(r: Rover) -> ActionResult:
    if (err := _alive(r, "safe_mode")) is not None:
        return err
    if r.safe:
        return _reject(r, "safe_mode", "already in safe mode")
    _enter_safe(r)
    return _ok(r, "safe_mode")


def order_shield(r: Rover) -> ActionResult:
    if (err := _alive(r, "shield")) is not None:
        return err
    if (err := _raise_shield(r, "shield")) is not None:
        return err
    r.cmd_shield = True
    return _ok(r, "shield", charges_left=r.shield_charges)


def order_unshield(r: Rover) -> ActionResult:
    if (err := _alive(r, "unshield")) is not None:
        return err
    if r.shield == "off":
        return _reject(r, "unshield", "the shield is not up")
    r.cmd_shield = False
    return _ok(r, "unshield")


def order_back_up(r: Rover) -> ActionResult:
    if (err := _alive(r, "back_up")) is not None:
        return err
    if not r.stuck:
        return _reject(r, "back_up", "rover is not stuck")
    return _start_recover(r)


ACTIONS = {
    "respond": respond,
    "sample": sample,
    "stop": order_stop,
    "drive": order_drive,
    "safe_mode": order_safe_mode,
    "shield": order_shield,
    "unshield": order_unshield,
    "back_up": order_back_up,
}


def dispatch(r: Rover, name: str, **args: Any) -> ActionResult:
    fn = ACTIONS.get(name)
    if fn is None:
        return _reject(r, name, f"unknown action {name!r}")
    try:
        return fn(r, **args)
    except TypeError as exc:
        return _reject(r, name, f"bad arguments: {exc}")
