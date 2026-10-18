"""The running game: rover + planner + Earth link + clock. No UI imports.

The screen calls `advance(dt)` once per frame and reads the rover and the events it returns.
The Earth link runs on sim ticks, so pausing and changing speed keep messages consistent.
"""

from __future__ import annotations

import random
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roverthinking.config import Settings
from roverthinking.decisions.log import DecisionLog
from roverthinking.earth import mission_control as mc
from roverthinking.earth.link import EarthLink, Message
from roverthinking.earth.providers import EarthModel, MockEarth
from roverthinking.game.desk import EarthDesk
from roverthinking.game.scenarios import Scenario, ScenarioEvent, random_cascade
from roverthinking.planners import PlannerContext, make_planner
from roverthinking.sim.hazards import POSITIONAL
from roverthinking.sim.rover import Rover, SimEvent
from roverthinking.sim.scoring import mission_score

TICK_RATE = 10  # sim ticks per real second at 1x
SPEEDS = {1: 1, 2: 2, 3: 4}  # key -> multiplier
MAX_TICKS_PER_FRAME = 60
SAMPLE_EVERY = 5  # ticks between sparkline samples
HISTORY = 40


@dataclass
class DecisionRecord:
    index: int
    tick: int
    data: dict[str, Any]
    snapshot: dict[str, Any]
    action: str = ""
    action_ok: bool = True


class GameSession:
    def __init__(
        self,
        settings: Settings,
        planner: str = "rules",
        seed: int = 0,
        ambient: float = 0.0,
        scenario: Scenario | None = None,
        decider: Any = None,
        earth: EarthModel | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self.settings = settings
        self.rover = Rover(seed=seed, random_rate=ambient)
        self.seed = seed
        self.link = EarthLink(settings.delay_real_seconds * TICK_RATE)
        self.log = DecisionLog.new(log_dir) if log_dir else DecisionLog(None)
        # model calls run in background threads so the screen never freezes
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="decider")
        self._earth_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="earth")
        self.decider = decider
        self.earth: EarthModel = earth or MockEarth()
        self.ready_plan = None  # a free-text scenario Earth has finished planning
        self._nl_jobs: list[tuple[str, Any, str]] = []
        self.desk = EarthDesk(self.rover, self.link, self.earth, self._earth_pool)
        self.ctx = PlannerContext(
            decider=decider,
            threshold=settings.confidence_threshold,
            executor=self._pool,
            ms_per_tick=1000 / TICK_RATE,
            reply_timeout_ticks=int(self.link.delay * 2 + 500),  # room for the model's own thinking
            escalate=self.desk.escalate,
        )
        self.planner_name = planner
        self.planner = make_planner(planner, self.ctx)
        self.desk.planner = self.planner
        self.speed_key = 1
        self.paused = False
        self._acc = 0.0
        self._cursor = 0
        self.decisions: list[DecisionRecord] = []
        self._open: dict[int, tuple[int, float, float, int]] = {}
        self._by_ref: dict[int, DecisionRecord] = {}
        self.history: dict[str, deque[float]] = {
            k: deque(maxlen=HISTORY) for k in ("speed", "wheels", "instruments", "nav")
        }
        self.scenario_name = "sandbox"
        self._fired: list[ScenarioEvent] = []
        self._rng = random.Random(seed + 1)
        if scenario:
            self.load(scenario)
        self._sample()

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._earth_pool.shutdown(wait=False, cancel_futures=True)

    # ---- derived ------------------------------------------------------------------------------

    @property
    def speed(self) -> int:
        return SPEEDS[self.speed_key]

    @property
    def now(self) -> float:
        """Sim time in ticks, including the fraction of the next tick."""
        return self.rover.tick_n + self._acc

    @property
    def score(self) -> int:
        return mission_score(self.rover)

    @property
    def delay_label(self) -> str:
        total = self.settings.light_delay_min * 60
        return f"{int(total // 60)}m {int(total % 60):02d}s"

    def eta_label(self, msg: Message) -> str:
        left = max(0.0, msg.arrive_at - self.now) / max(self.link.delay, 1e-6)
        s = left * self.settings.light_delay_min * 60
        return f"{int(s // 60)}m {int(s % 60):02d}s"

    # ---- controls -----------------------------------------------------------------------------

    def set_speed(self, key: int) -> None:
        if key in SPEEDS:
            self.speed_key = key

    def toggle_pause(self) -> bool:
        self.paused = not self.paused
        return self.paused

    def set_planner(self, name: str) -> None:
        self.planner = make_planner(name, self.ctx)
        self.desk.planner = self.planner
        self.planner_name = name
        self.rover.emit("planner_changed", planner=name)

    def load(self, sc: Scenario) -> None:
        self.scenario_name = sc.name
        for e in sc.events:
            self.rover.schedule_hazard(self.rover.tick_n + e.at_tick, e.kind, e.ahead, e.severity)

    def cast(self, kind: str, ahead: float | None = None, severity: int | None = None) -> None:
        self.rover.start_hazard(kind, ahead, severity)

    def schedule(self, ev: ScenarioEvent, delay_s: float = 0.0) -> None:
        ev.validate()
        at = self.rover.tick_n + max(1, int(delay_s * TICK_RATE)) + ev.at_tick
        self.rover.schedule_hazard(at, ev.kind, ev.ahead, ev.severity)

    def remove_hazard(self, hid: int) -> bool:
        return self.rover.remove_hazard(hid)

    def clear_hazards(self) -> int:
        """Remove every active hazard and cancel everything scheduled."""
        n = sum(1 for h in list(self.rover.active_hazards()) if self.rover.remove_hazard(h.id))
        n += len(self.rover.schedule)
        self.rover.schedule.clear()
        return n

    def cascade(self) -> Scenario:
        sc = random_cascade(self._rng)
        for e in sc.events:
            self.schedule(e, 0.0)
        return sc

    def recorded_scenario(self, name: str) -> Scenario:
        return Scenario(name, self.seed, "Recorded from a play session.", list(self._fired))

    def send_order(self, text: str) -> str:
        """Send an order across the light delay. Strict commands go straight out; anything else is
        read by Earth (when it can read text) in the background first."""
        try:
            self.desk.send_parsed(mc.parse_order(text))
            return ""
        except mc.OrderError as exc:
            if not self.earth.interprets_text:
                self.rover.emit("order_invalid", text=text, reason=str(exc))
                return str(exc)
        self.rover.emit("order_interpreting", text=text, earth=self.earth.name)
        fut = self._earth_pool.submit(self.earth.parse_order, text, self.rover.snapshot())
        self._nl_jobs.append(("order", fut, text))
        return ""

    def plan_scenario(self, text: str) -> None:
        """Ask Earth to turn free text into a hazard schedule; the result lands in `ready_plan`."""
        self.rover.emit("scenario_planning", text=text, earth=self.earth.name)
        fut = self._earth_pool.submit(self.earth.plan_scenario, text)
        self._nl_jobs.append(("scenario", fut, text))

    def schedule_plan(self, plan) -> Scenario:
        """Turn a confirmed ScenarioPlan into scheduled hazards; returns it for saving."""
        events = []
        for e in plan.events:
            ahead = e.ahead_m
            if e.kind in POSITIONAL:
                ahead = min(max(ahead or 110.0, 40.0), 400.0)
            else:
                ahead = None
            events.append(ScenarioEvent(int(e.after_seconds * TICK_RATE), e.kind, ahead, e.severity))
        sc = Scenario(plan.title, self.seed, plan.description, events)
        sc.validate()
        self.scenario_name = sc.name
        for ev in events:
            self.rover.schedule_hazard(self.rover.tick_n + max(1, ev.at_tick), ev.kind, ev.ahead, ev.severity)
        return sc

    def _poll_language_jobs(self) -> None:
        for job in list(self._nl_jobs):
            kind, fut, text = job
            if not fut.done():
                continue
            self._nl_jobs.remove(job)
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001 - Earth failing must not crash the game
                self.rover.emit(
                    "order_invalid" if kind == "order" else "scenario_failed",
                    text=text,
                    reason=f"Earth could not read that: {str(exc)[:90]}",
                )
                continue
            if kind == "order":
                try:
                    order = mc.order_from_plan(res)
                except mc.OrderError as exc:
                    self.rover.emit("order_invalid", text=text, reason=str(exc))
                    continue
                self.rover.emit(
                    "order_interpreted", text=text, order=order.text, note=getattr(res, "explanation", "")
                )
                self.desk.send_parsed(order)
            else:
                self.ready_plan = res

    # ---- the frame ------------------------------------------------------------------------------

    def advance(self, dt: float) -> list[SimEvent]:
        """Run the simulation for `dt` real seconds; return the log entries it produced."""
        r = self.rover
        if not self.paused and not r.finished:
            self._acc += dt * TICK_RATE * self.speed
            n = min(int(self._acc), MAX_TICKS_PER_FRAME)
            self._acc = 0.0 if int(self._acc) > MAX_TICKS_PER_FRAME else self._acc - n
            for _ in range(n):
                self._one_tick()
        self._poll_language_jobs()
        new, self._cursor = r.events_since(self._cursor)
        for e in new:
            if e.kind == "decision":
                snap = e.data.get("state") or r.snapshot()
                rec = DecisionRecord(len(self.decisions), e.tick, dict(e.data), snap)
                self.decisions.append(rec)
                if e.data.get("ref") is not None:
                    self._by_ref[e.data["ref"]] = rec
                did = self.log.decision({**e.data, "tick": e.tick}, snap)
                if e.data.get("hazard_id") is not None:
                    self._open[e.data["hazard_id"]] = (did, r.damage_taken, r.x, e.tick)
            elif e.kind == "decision_action" and e.data.get("ref") in self._by_ref:
                rec = self._by_ref[e.data["ref"]]
                rec.action, rec.action_ok = e.data["summary"], e.data["ok"]
            elif e.kind == "hazard_end" and e.data["id"] in self._open:
                did, dmg, x0, t0 = self._open.pop(e.data["id"])
                self.log.outcome(
                    did,
                    e.data["how"],
                    damage=round(r.damage_taken - dmg, 1),
                    distance_m=round(r.x - x0),
                    ticks=e.tick - t0,
                    rover_alive=r.alive,
                )
            elif e.kind == "hazard_start":
                self._fired.append(ScenarioEvent(e.tick, e.data["hazard"], _ahead(e, r), e.data["severity"]))
        return new

    def _one_tick(self) -> None:
        self.rover.tick()
        self.desk.tick()
        self.planner.step(self.rover)
        if self.rover.tick_n % SAMPLE_EVERY == 0:
            self._sample()

    def _sample(self) -> None:
        r = self.rover
        h = self.history
        h["speed"].append(r.velocity)
        h["wheels"].append(r.wheels)
        h["instruments"].append(r.instruments)
        h["nav"].append(r.nav)


def _ahead(e: SimEvent, r: Rover) -> float | None:
    x = e.data.get("x")
    return None if x is None else max(10.0, float(x))
