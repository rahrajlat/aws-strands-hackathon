"""Headless runs: the same loop the game uses, with no screen."""

from __future__ import annotations

from dataclasses import dataclass

from roverthinking.planners import make_planner
from roverthinking.sim.rover import Rover
from roverthinking.sim.scoring import RunSummary, summarize

MAX_TICKS = 4000


@dataclass(frozen=True)
class HeadlessResult:
    summary: RunSummary
    hazards: int
    decisions: int
    correct: int
    actions_rejected: int
    escalations: int = 0
    held_orders: int = 0


def build_planner(rover: Rover, name: str, decider=None, threshold: float = 0.70, delay_ticks=150):
    """A planner plus its Earth desk, wired for an offline (inline, deterministic) run."""
    from roverthinking.decisions.model import make_decider
    from roverthinking.earth.link import EarthLink
    from roverthinking.earth.providers import MockEarth
    from roverthinking.game.desk import EarthDesk
    from roverthinking.planners import PlannerContext

    link = EarthLink(delay_ticks)
    desk = EarthDesk(rover, link, MockEarth(), pool=None)
    if decider is None:
        decider, _ = make_decider("mock", "")
    ctx = PlannerContext(
        decider=decider,
        threshold=threshold,
        executor=None,
        ms_per_tick=100.0,
        reply_timeout_ticks=int(delay_ticks * 2 + 500),
        escalate=desk.escalate,
    )
    planner = make_planner(name, ctx)
    desk.planner = planner
    return planner, desk


def run_headless(
    planner: str = "rules",
    seed: int = 0,
    random_rate: float = 1 / 150,
    max_ticks: int = MAX_TICKS,
    decider=None,
    threshold: float = 0.70,
) -> HeadlessResult:
    rover = Rover(seed=seed, random_rate=random_rate)
    pl, desk = build_planner(rover, planner, decider, threshold)
    return run_rover(rover, pl, max_ticks, desk)


def run_rover(rover: Rover, planner, max_ticks: int = MAX_TICKS, desk=None) -> HeadlessResult:
    while not rover.finished and rover.tick_n < max_ticks:
        rover.tick()
        if desk is not None:
            desk.tick()
        planner.step(rover)
    kinds = [e.kind for e in rover.log]
    decisions = [e.data for e in rover.log if e.kind == "decision" and e.data.get("ideal") is not None]
    return HeadlessResult(
        summary=summarize(rover),
        hazards=kinds.count("hazard_start"),
        decisions=len(decisions),
        correct=sum(1 for d in decisions if d["choice"] == d["ideal"]),
        actions_rejected=kinds.count("action_rejected"),
        escalations=sum(1 for e in rover.log if e.kind == "decision" and e.data.get("escalated")),
        held_orders=kinds.count("order_held"),
    )
