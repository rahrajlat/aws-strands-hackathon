"""Turning a rover snapshot into the text Decider reads as its `state`."""

from __future__ import annotations

import json
from typing import Any

KIND_DESC = {
    "sand_trap": "a patch of soft sand on the ground that can bog down the wheels",
    "rockfall": "a boulder field on the ground",
    "steep_slope": "a steep slope on the ground",
    "dust_storm": "a dust storm, which is weather",
    "solar_flare": "a solar flare, which is radiation from the sun",
    "meteor_shower": "a meteor shower, rocks falling from space",
    "sensor_fault": "a fault in the rover's own navigation sensors",
    "strange_rock": "an unusual rock worth examining",
}


def as_json(state: dict[str, Any]) -> str:
    return json.dumps(state, sort_keys=True, default=str)


def as_prose(state: dict[str, Any]) -> str:
    """A short plain-English briefing. Small classifiers read prose better than raw JSON."""
    if "order" in state:  # a stale-order check carries the order too
        order = f"Order from Earth: {state['order']}. "
        state = {k: v for k, v in state.items() if k != "order"}
    else:
        order = ""
    s = state
    parts = [
        f"Rover status: wheels {s['wheels_pct']}% healthy, "
        f"instruments {s['instruments_pct']}%, navigation cameras {s['nav_cameras_pct']}%, "
        f"{s['progress_m']} of {s['goal_m']} m travelled."
    ]
    sh = s.get("shield") or {}
    flags = []
    if sh:
        flags.append(f"Shield charges left: {sh['charges']} of {sh['of']}.")
        if sh.get("state") not in (None, "off"):
            flags.append(f"The shield is {sh['state']}.")
    if s.get("stuck"):
        flags.append("The rover is STUCK and cannot move.")
    if s.get("safe_mode"):
        flags.append("The rover is in safe mode.")
    if not s.get("comms_ok", True):
        flags.append("Communications with Earth are down.")
    if s.get("mode") not in (None, "driving"):
        flags.append(f"The rover is currently {s['mode']}.")
    parts += flags
    for h in s.get("hazards", []):
        where = "affecting the rover right now" if h["distance_m"] is None else f"{h['distance_m']} m ahead"
        sev = {1: "minor", 2: "major", 3: "catastrophic"}[h["severity"]]
        desc = KIND_DESC.get(h["kind"], "")
        line = f"Hazard: {sev} {h['kind'].replace('_', ' ')} ({desc}), {where}"
        if h.get("length_m"):
            line += f", about {h['length_m']} m long"
        if h.get("description"):
            line += f". It looks like: {h['description']}"
        parts.append(line + ".")
    if s.get("other_hazards"):
        parts.append(f"{s['other_hazards']} other hazard(s) are also in view.")
    return order + " ".join(parts)
