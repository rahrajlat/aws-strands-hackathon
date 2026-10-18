"""Earth Mission Control: turning the player's typed orders into guarded actions.

Free-text orders and scenarios (via the Earth provider) arrive in a later milestone; this
module is the strict command grammar every order must reduce to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from roverthinking.sim.rover import INSTRUMENTS


class OrderError(ValueError):
    """The order could not be understood; the message says why."""


@dataclass(frozen=True)
class Order:
    action: str
    args: dict[str, Any]
    text: str


HELP = (
    "stop | go [slow|normal] | slow | safe mode | shield | shield down | back up | "
    "sample <camera|spectrometer|drill|skip>"
)


def parse_order(text: str) -> Order:
    raw = " ".join(text.strip().lstrip(">").lower().split())
    if not raw:
        raise OrderError("empty order")
    words = raw.split()
    head, rest = words[0], words[1:]
    if raw in ("safe mode", "safe", "safemode"):
        return Order("safe_mode", {}, "safe mode")
    if raw in ("back up", "backup", "reverse", "back"):
        return Order("back_up", {}, "back up")
    if raw in ("shield", "shield up", "shield on", "raise shield"):
        return Order("shield", {}, "shield up")
    if raw in ("shield down", "shield off", "unshield", "lower shield"):
        return Order("unshield", {}, "shield down")
    if head == "stop" and not rest:
        return Order("stop", {}, "stop")
    if head == "slow" and not rest:
        return Order("drive", {"speed": "slow"}, "drive slow")
    if head in ("go", "drive", "resume") and len(rest) <= 1:
        speed = rest[0] if rest else "normal"
        if speed not in ("normal", "slow"):
            raise OrderError(f"unknown speed {speed!r} (normal or slow)")
        return Order("drive", {"speed": speed}, f"drive {speed}")
    if head == "sample" and len(rest) == 1:
        if rest[0] not in INSTRUMENTS:
            raise OrderError(f"unknown instrument {rest[0]!r} ({', '.join(INSTRUMENTS)})")
        return Order("sample", {"instrument": rest[0]}, f"sample with {rest[0]}")
    raise OrderError(f"not understood. Orders: {HELP}")


def order_from_plan(plan) -> Order:
    """Turn Earth's structured reading of a free-text order into a strict Order."""
    if not plan.understood or plan.action == "none":
        raise OrderError(plan.explanation or "Earth could not turn that into an order")
    tag = " (interpreted)"
    if plan.action == "drive":
        return Order("drive", {"speed": plan.speed}, f"drive {plan.speed}{tag}")
    if plan.action == "sample":
        return Order("sample", {"instrument": plan.instrument}, f"sample with {plan.instrument}{tag}")
    return Order(plan.action, {}, plan.action.replace("_", " ") + tag)
