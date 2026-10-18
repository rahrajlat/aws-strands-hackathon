"""Sensible offline answers for MockDecider, so the Steward runs with no model.

Written from the *snapshot*, the same facts the real Decider is shown. Confidence drops on
ambiguous cases, and ambiguous answers are sometimes wrong, so the mock exercises
Act-or-Escalate the way a real small model does. These are not Decider numbers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from roverthinking.decisions.model import Question

STATIC = ("rockfall", "sand_trap", "steep_slope")


def _flip(state: dict[str, Any], key: str, p: float) -> bool:
    h = hashlib.sha256((json.dumps(state, sort_keys=True, default=str) + key).encode()).digest()
    return h[2] / 255 < p


def _focus(state: dict[str, Any]) -> dict[str, Any]:
    hz = state.get("hazards") or [{}]
    return hz[0]


def mock_rules(state: dict[str, Any], q: Question) -> tuple[str, float] | None:
    h = _focus(state)
    kind, sev = h.get("kind", ""), h.get("severity", 1)
    wheels, nav = state["wheels_pct"], state["nav_cameras_pct"]
    ambiguous = (sev == 2) or 45 <= wheels <= 70
    conf = 0.62 if ambiguous else 0.9
    if q.key == "is_external_threat":
        return ("yes" if kind in ("solar_flare", "meteor_shower", "dust_storm") else "no"), 0.9
    if q.key == "is_severe":
        yes = sev >= 2
        if sev == 2 and _flip(state, q.key, 0.3):
            yes = not yes
        return ("yes" if yes else "no"), (0.66 if sev == 2 else 0.9)
    if q.key == "needs_safe_mode":
        yes = kind == "solar_flare" or (kind == "sensor_fault" and (sev >= 2 or nav <= 60))
        return ("yes" if yes else "no"), (0.66 if kind == "sensor_fault" else 0.9)
    if q.key == "avoid_hazard":
        yes = kind in STATIC and (sev >= 2 or wheels < 60)
        if ambiguous and _flip(state, q.key, 0.3):
            yes = not yes
        return ("yes" if yes else "no"), conf
    if q.key == "wait_it_out":
        yes = (kind == "dust_storm" and sev >= 2) or (kind == "meteor_shower")
        if ambiguous and _flip(state, q.key, 0.3):
            yes = not yes
        return ("yes" if yes else "no"), conf
    if q.key == "drill_worthwhile":
        desc = h.get("description", "")
        yes = "layered" in desc or "clay" in desc
        return ("yes" if yes else "no"), 0.8
    if q.key == "can_wait":
        yes = kind in STATIC + ("strange_rock",) and not state.get("stuck")
        return ("yes" if yes else "no"), 0.8
    if q.key == "order_valid":
        return _order(state)
    return None


def _order(state: dict[str, Any]) -> tuple[str, float]:
    text = state.get("order", "")
    haz = [x for x in state.get("hazards", []) if x["kind"] != "strange_rock"]
    severe = [x for x in haz if x["severity"] >= 2]
    shield = state.get("shield") or {}
    if "stop" in text or "safe" in text or "unshield" in text or "shield down" in text:
        return "yes", 0.9
    if "shield" in text:
        external = [x for x in severe if x["kind"] in ("dust_storm", "solar_flare", "meteor_shower")]
        return ("yes" if external and shield.get("charges", 0) > 0 else "no"), 0.8
    if "back up" in text:
        return ("yes" if state.get("stuck") else "no"), 0.9
    if "drive slow" in text:
        return ("no" if any(x["severity"] == 3 for x in haz) else "yes"), 0.8
    if "drive" in text:
        bad = severe or state.get("stuck") or state.get("safe_mode") or not state.get("comms_ok")
        bad = bad or shield.get("state", "off") != "off"
        return ("no" if bad else "yes"), 0.8
    if "sample" in text:
        rocks = [x for x in state.get("hazards", []) if x["kind"] == "strange_rock"]
        return ("yes" if rocks else "no"), 0.7
    return "yes", 0.6
