"""Terrain height as a function of distance along the traverse. Pure maths."""

from __future__ import annotations

import math

from roverthinking.sim.rover import Rover

METRES_PER_CELL = 3.0


def base_offset(wx: float) -> float:
    return 0.7 * math.sin(wx / 83.0) + 0.45 * math.sin(wx / 37.0 + 2.0)


def offset(wx: float, rover: Rover) -> int:
    """Rows above the baseline at world position `wx` (metres)."""
    o = base_offset(wx)
    for h in rover.hazards:
        if h.resolved and h.x is not None and wx < h.x + h.length:
            pass
        if h.x is None or h.length <= 0:
            continue
        if h.x <= wx <= h.x + h.length:
            t = (wx - h.x) / h.length
            if h.kind == "steep_slope":
                o += (2 + h.severity) * math.sin(math.pi * t)
            elif h.kind == "sand_trap":
                o -= 1.0 * min(1.0, 4 * math.sin(math.pi * t))
    return round(o)


def hash01(*vals: int) -> float:
    n = 0x9E3779B1
    for v in vals:
        n = (n ^ (v & 0xFFFFFFFF)) * 0x85EBCA6B & 0xFFFFFFFF
        n ^= n >> 13
    return (n & 0xFFFF) / 0xFFFF
