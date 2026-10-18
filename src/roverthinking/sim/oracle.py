"""What the ideal response is. Used to grade decisions (and to drive the offline mock).

This is ground truth the Steward never sees: it has to infer the right call from the state.
"""

from __future__ import annotations

from roverthinking.sim.hazards import best_instrument
from roverthinking.sim.rover import Hazard, Rover

EXTERNAL = ("dust_storm", "solar_flare", "meteor_shower")


def ideal_triage(r: Rover, h: Hazard) -> str:
    k, s = h.kind, h.severity
    if r.stuck:
        return "back_up"
    shield = r.shield_charges > 0 or r.shield != "off"
    if k == "dust_storm":
        if s == 3:
            return "shield" if shield else "stop_and_wait"
        return "stop_and_wait" if s == 2 else "slow"
    if k == "rockfall":
        return "slow" if s == 1 and r.wheels > 50 else "reroute"
    if k == "sand_trap":
        return "slow" if s == 1 and r.wheels > 60 else "reroute"
    if k == "steep_slope":
        return "slow" if s == 1 else "reroute"
    if k == "sensor_fault":
        return "slow" if s == 1 and r.nav > 60 else "safe_mode"
    if k == "solar_flare":
        if s >= 2:
            return "shield" if shield else "safe_mode"
        return "stop_and_wait"
    if k == "meteor_shower":
        if s >= 2:
            return "shield" if shield else "stop_and_wait"
        return "slow"
    return "slow"


def acceptable_triage(r: Rover, h: Hazard) -> set[str]:
    """Every response a sensible operator could defend. Used to grade decisions fairly."""
    best = ideal_triage(r, h)
    ok = {best}
    k, s = h.kind, h.severity
    if r.stuck:
        return ok
    if k == "dust_storm":
        if s == 2:
            ok |= {"slow", "shield"}
        if s == 3:
            ok |= {"stop_and_wait"}
        if s == 1:
            ok |= {"stop_and_wait"}
    elif k in ("rockfall", "sand_trap"):
        if s == 2 and r.wheels > 60:
            ok |= {"slow", "reroute"}
        if s == 3:
            ok |= {"stop_and_wait"}
    elif k == "steep_slope":
        if s == 2:
            ok |= {"slow", "reroute"}
    elif k == "sensor_fault":
        ok |= {"slow", "safe_mode"} if s <= 2 else set()
    elif k == "solar_flare":
        ok |= {"safe_mode", "stop_and_wait", "shield"} if s >= 2 else {"safe_mode"}
    elif k == "meteor_shower":
        ok |= {"shield", "stop_and_wait"} if s >= 2 else {"stop_and_wait"}
    return ok


def ideal_instrument(h: Hazard) -> str:
    return best_instrument(h.rock_type)


def ambiguity(r: Rover, h: Hazard) -> float:
    """0 = clear-cut, 1 = a coin flip. Drives the mock's confidence."""
    a = 0.0
    if h.severity == 2:
        a += 0.35
    if h.kind in ("rockfall", "sand_trap") and 45 <= r.wheels <= 70:
        a += 0.3
    if h.kind == "sensor_fault" and 45 <= r.nav <= 75:
        a += 0.3
    if h.kind == "strange_rock":
        a += 0.25
    return min(a, 0.95)


def can_wait(r: Rover, h: Hazard) -> bool:
    """Is stopping to ask Earth affordable? Fixed terrain with the link up: yes."""
    if r.stuck or not r.comms_ok:
        return False
    return h.kind in ("rockfall", "sand_trap", "steep_slope", "strange_rock")


def order_valid(r: Rover, action: str, args: dict) -> bool:
    """Is an order from Earth still safe and sensible given what the rover now faces?"""
    haz = r.detected_hazards()
    severe = [h for h in haz if h.severity >= 2 and h.kind != "strange_rock"]
    if action in ("stop", "safe_mode", "unshield"):
        return True
    if action == "shield":
        return r.shield_charges > 0 and any(h.kind in EXTERNAL and h.severity >= 2 for h in haz)
    if action == "drive":
        if r.safe or r.flare_active or r.shield != "off":
            return False
        if args.get("speed") == "slow":
            return not any(h.severity == 3 and h.kind != "strange_rock" for h in haz)
        return not severe and not r.stuck
    if action == "back_up":
        return r.stuck
    if action == "sample":
        rocks = [h for h in haz if h.kind == "strange_rock"]
        if not rocks:
            return False
        return not (args.get("instrument") == "drill" and rocks[0].rock_type in ("ice", "metallic"))
    return True
