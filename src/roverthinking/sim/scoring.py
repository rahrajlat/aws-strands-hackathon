"""Mission score and run summaries."""

from __future__ import annotations

from dataclasses import dataclass

from roverthinking.sim.rover import GOAL_M, Rover


def mission_score(r: Rover) -> int:
    """Progress (up to 600) + science (40 per point) + condition (up to 200); 0 if lost."""
    if not r.alive:
        return int(r.progress * 150)
    return int(r.progress * 600 + r.science * 40 + r.health() * 2)


@dataclass(frozen=True)
class RunSummary:
    reached_goal: bool
    alive: bool
    dead_reason: str
    progress_m: int
    ticks: int
    science: int
    samples: int
    wheels: float
    instruments: float
    shield_charges: int
    damage: float
    score: int


def summarize(r: Rover) -> RunSummary:
    return RunSummary(
        reached_goal=r.mode == "done",
        alive=r.alive,
        dead_reason=r.dead_reason,
        progress_m=int(min(r.x, GOAL_M)),
        ticks=r.tick_n,
        science=r.science,
        samples=r.samples,
        wheels=round(r.wheels, 1),
        instruments=round(r.instruments, 1),
        shield_charges=r.shield_charges,
        damage=round(r.damage_taken, 1),
        score=mission_score(r),
    )
