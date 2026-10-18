"""Particles, flashes, shake and fading labels, driven by events from the simulation.

The simulation never knows about this module. Coordinates by `space`:
  world   x metres along the traverse, y rows above the ground baseline
  rover   x cells from the rover's centre column, y rows above the rover's ground
  screen  x, y cells from the top-left of the view
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from roverthinking.sim.rover import Rover, SimEvent
from roverthinking.tui.canvas import RGB

SAND: RGB = (214, 170, 110)
DUST: RGB = (200, 150, 100)
SPARK: RGB = (255, 220, 120)
RED: RGB = (255, 80, 60)
GREEN: RGB = (120, 235, 150)
AMBER: RGB = (255, 190, 80)
CYAN: RGB = (110, 220, 255)

VIOLET: RGB = (200, 150, 255)
ROCK: RGB = (150, 125, 110)


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    ch: str
    color: RGB
    ttl: float
    space: str = "world"
    gravity: float = 0.0
    land: bool = False
    age: float = 0.0


@dataclass
class Bubble:
    text: str
    color: RGB
    ttl: float = 3.6
    age: float = 0.0


@dataclass
class Label:
    x: float
    y: float
    text: str
    color: RGB
    ttl: float
    space: str = "rover"
    age: float = 0.0
    rise: float = 1.5


ALARM_LINES = {
    "rockfall": "Boulders ahead!",
    "sand_trap": "Soft sand ahead...",
    "steep_slope": "That slope looks steep!",
    "dust_storm": "A dust storm is rolling in!",
    "solar_flare": "Solar flare! I'm losing the radio.",
    "meteor_shower": "Meteors incoming!",
    "sensor_fault": "My cameras are glitching...",
    "strange_rock": "Ooh, an interesting rock!",
}
SPEECH = {
    "reroute": "I'll go around it.",
    "slow": "Easy does it... crawling through.",
    "stop_and_wait": "I'll stop and wait this out.",
    "safe_mode": "Switching to safe mode. Back soon!",
    "shield": "Shields up!",
    "back_up": "Backing out...",
    "proceed": "Pressing on.",
    "skip": "Not worth it. Skipping.",
    "hold": "That order is out of date. Holding it.",
    "run": "Copy that, Mission Control.",
    "apply": "Good advice. Doing it.",
}
INSTRUMENT_NAMES = ("camera", "spectrometer", "drill")


def rng_x(rng: random.Random) -> float:
    return rng.uniform(-10, 10)


class EffectsLayer:
    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.particles: list[Particle] = []
        self.labels: list[Label] = []
        self.flash: tuple[RGB, float] = ((255, 255, 255), 0.0)
        self.shake_t = 0.0
        self.shake_amp = 1
        self.comms_pulse = 0.0
        self.shield_hit = 0.0
        self.dome: tuple[int, int, int, int] | None = None  # (centre col, ground row, rx, ry)
        self.hurt = 0.0
        self.joy = 0.0
        self.pending: set[int] = set()
        self.bubble: Bubble | None = None
        self._speech: list[Bubble] = []
        self._idle = 14.0
        self._acc: dict[str, float] = {}
        self.particles_new: list[Particle] = []

    # ---- helpers ---------------------------------------------------------------------------

    def _rate(self, key: str, per_sec: float, dt: float) -> int:
        self._acc[key] = self._acc.get(key, 0.0) + per_sec * dt
        n = int(self._acc[key])
        self._acc[key] -= n
        return n

    def burst(self, x, y, n, chars, color, speed=6.0, ttl=0.8, gravity=14.0, space="world", up=1.0):
        for _ in range(n):
            ang = self.rng.uniform(0, math.pi)
            sp = self.rng.uniform(0.3, 1.0) * speed
            self.particles.append(
                Particle(
                    x,
                    y,
                    math.cos(ang) * sp * 1.6,
                    math.sin(ang) * sp * up,
                    self.rng.choice(chars),
                    color,
                    ttl * self.rng.uniform(0.6, 1.2),
                    space,
                    gravity,
                )
            )

    def label(self, x, y, text, color, ttl=2.4, space="rover") -> None:
        # keep stacked labels from overlapping
        for lb in self.labels:
            if lb.space == space and abs(lb.y + lb.age * lb.rise - y) < 1.5 and lb.age < 0.6:
                y += 1.5
        self.labels.append(Label(x, y, text, color, ttl, space))

    def say(self, text: str, color: RGB = CYAN, ttl: float = 3.6) -> None:
        """Rovy says something; lines queue up so each gets read."""
        b = Bubble(text, color, ttl)
        if self.bubble is None:
            self.bubble = b
        else:
            self._speech = [*self._speech[-2:], b]
            self.bubble.ttl = min(self.bubble.ttl, self.bubble.age + 1.1)

    def do_flash(self, color: RGB, alpha: float) -> None:
        if alpha > self.flash[1]:
            self.flash = (color, alpha)

    def do_shake(self, t: float, amp: int = 1) -> None:
        if t > self.shake_t:
            self.shake_t, self.shake_amp = t, amp

    # ---- events from the simulation ----------------------------------------------------------

    def ingest(self, events: list[SimEvent], rover: Rover) -> None:
        for e in events:
            d = e.data
            k = e.kind
            if k == "hazard_start":
                self._hazard_start(d, rover)
            elif k == "alarm" and d.get("reason") == "detected":
                line = ALARM_LINES.get(d["hazard"])
                if line:
                    self.say(line, CYAN if d["hazard"] == "strange_rock" else AMBER)
            elif k == "decision_pending":
                self.pending.add(d["ref"])
            elif k == "decision":
                self.pending.discard(d.get("ref"))
                self._decision(d)
            elif k == "decision_action":
                self.pending.discard(d.get("ref"))
            elif k == "action_rejected":
                self.say(f"I can't do that: {d.get('reason', 'rejected')[:40]}", RED)
            elif k == "damage" and d["amount"] >= 1.0:
                self.burst(0, 1, 6, "*+", RED, 5, 0.6, space="rover")
                self.label(0, 5, f"-{d['amount']:.0f} {d['part']}", RED, 1.8)
                self.hurt = 0.8
                if d["amount"] >= 4:
                    self.do_shake(0.25, 1)
                    self.say("Ouch!", RED, 1.6)
            elif k == "stuck":
                self.say("I'm stuck!", RED)
                self.do_shake(0.3, 1)
            elif k == "freed":
                self.say("Phew, free again!", GREEN, 2.4)
            elif k == "sampling":
                self.say(f"Sampling with the {d['instrument']}...", CYAN)
            elif k == "sample_done":
                self.burst(8, 2, 10, "*+.", GREEN, 5, 1.0, space="rover")
                if d["points"]:
                    self.joy = 2.5
                    self.say(f"Great sample! +{d['points']} science", GREEN)
                else:
                    self.say("Hmm, nothing useful there.", AMBER)
            elif k == "impact":
                if d["blocked"]:
                    self.shield_hit = 1.0
                    self.burst(rng_x(self.rng), 4, 4, "*o", CYAN, 5, 0.5, space="rover")
                else:
                    self.do_flash(RED, 0.3)
                    self.do_shake(0.2, 1)
                    self.burst(0, 3, 6, "*+", RED, 6, 0.6, space="rover")
            elif k == "shield_up":
                self.say("The shield is up.", CYAN, 2.2)
            elif k == "safe_mode_exit":
                self.say("Back online.", GREEN, 2.0)
            elif k == "hazard_end" and d.get("how") in ("ended", "rebooted"):
                self.say("All clear.", GREEN, 2.0)
            elif k == "reply_stale":
                self.say("Earth's advice is out of date.", AMBER)
            elif k == "msg_sent" or k == "msg_received":
                self.comms_pulse = 1.2
            elif k == "rover_dead":
                self.do_flash(RED, 0.5)
                self.say("Signal... lost...", RED, 8.0)
            elif k == "goal_reached":
                self.burst(0, 2, 40, "*+o", GREEN, 14, 2.0, space="rover", up=1.2)
                self.joy = 99.0
                self.say("I made it! Science site reached!", GREEN, 8.0)

    def _hazard_start(self, d: dict, rover: Rover) -> None:
        kind, x = d["hazard"], d.get("x")
        if kind == "rockfall" and x is not None:
            for _ in range(5 + 3 * d["severity"]):
                self.particles.append(
                    Particle(
                        x + self.rng.uniform(0, max(d["length"], 4)),
                        22 + self.rng.uniform(0, 14),
                        0,
                        -self.rng.uniform(12, 20),
                        self.rng.choice("o●"),
                        ROCK,
                        3.0,
                        "world",
                        0.0,
                        land=True,
                    )
                )
        elif kind == "solar_flare":
            self.do_flash((255, 245, 255), 0.9)
        elif kind == "meteor_shower":
            self.label(0, 7, "METEOR SHOWER", AMBER, 3.0)
        elif kind == "dust_storm":
            self.label(0, 7, "DUST STORM", AMBER, 3.0)

    def _decision(self, d: dict) -> None:
        choice = d["choice"]
        if d.get("escalated"):
            self.say("I'm not sure... asking Mission Control.", AMBER, 4.0)
            return
        line = SPEECH.get(choice) or (f"Let's use the {choice}!" if choice in INSTRUMENT_NAMES else "")
        if d.get("question") == "order_valid":
            line = SPEECH["hold"] if choice == "hold" else SPEECH["run"]
        if not line:
            return
        if d.get("under_uncertainty"):
            line = "Not sure, but... " + line[0].lower() + line[1:]
        self.say(line, GREEN if d.get("confidence", 1) >= 0.7 else AMBER, 3.6)

    # ---- per-frame update ---------------------------------------------------------------------

    def _voice(self, dt: float, rover: Rover) -> None:
        self.hurt = max(0.0, self.hurt - dt)
        self.joy = max(0.0, self.joy - dt)
        if self.bubble is not None:
            self.bubble.age += dt
            if self.bubble.age >= self.bubble.ttl:
                self.bubble = self._speech.pop(0) if self._speech else None
        elif self._speech:
            self.bubble = self._speech.pop(0)
        self._idle -= dt
        if self._idle <= 0 and self.bubble is None and rover.mode == "driving":
            lines = (
                "All systems nominal.",
                f"Sol {rover.sol}. Still rolling.",
                "Beep boop. Scanning the horizon.",
                "Mars is quiet today.",
            )
            self.say(self.rng.choice(lines), (150, 175, 200), 3.0)
            self._idle = self.rng.uniform(22, 36)

    def update(self, dt: float, rover: Rover, w: int, h: int, t: float) -> None:
        if dt <= 0:
            return
        self._voice(dt, rover)
        rng = self.rng
        dust = 1.0 - rover.visibility if rover.visibility < 1.0 else 0.0
        if dust > 0.05:
            for _ in range(self._rate("dust", dust * w * 0.9, dt)):
                vx = rng.uniform(30, 55)
                self.particles.append(
                    Particle(
                        -1,
                        rng.uniform(1, h - 2),
                        vx,
                        0,
                        rng.choice(":.,'"),
                        DUST,
                        (w + 2) / vx,
                        "screen",
                    )
                )
        shower = max((hz.severity for hz in rover.active_hazards() if hz.kind == "meteor_shower"), default=0)
        if shower:
            for _ in range(self._rate("meteor", 3.0 * shower, dt)):
                x0, vx, vy = rng.uniform(-w * 0.2, w), rng.uniform(22, 32), rng.uniform(14, 20)
                for k, ch in enumerate("\\.."):
                    self.particles.append(
                        Particle(
                            x0 - k * 1.6,
                            -k * 1.0,
                            vx,
                            vy,
                            ch,
                            (255, 230 - 40 * k, 160 - 40 * k),
                            1.2,
                            "screen",
                        )
                    )
        if rover.flare_active and t % 1.4 < dt:
            self.do_flash(VIOLET, 0.3)
        if rover.velocity > 0:
            for _ in range(self._rate("wheel", rover.velocity * 5, dt)):
                self.particles.append(
                    Particle(
                        rng.uniform(-9, -6),
                        0.5,
                        rng.uniform(-6, -1),
                        rng.uniform(1, 4),
                        rng.choice(".,"),
                        SAND,
                        0.6,
                        "rover",
                        8.0,
                    )
                )
        if rover.stuck:
            for _ in range(self._rate("spin", 30, dt)):
                self.particles.append(
                    Particle(
                        rng.choice((-5, 0, 5)),
                        0.5,
                        rng.uniform(-9, -2),
                        rng.uniform(5, 11),
                        rng.choice(".:"),
                        SAND,
                        0.8,
                        "rover",
                        25.0,
                    )
                )
        job = rover.job
        if job is not None and job.kind == "sample":
            for _ in range(self._rate("arm", 16, dt)):
                self.particles.append(
                    Particle(
                        20,
                        0.5,
                        rng.uniform(-4, 6),
                        rng.uniform(3, 9),
                        rng.choice("*+."),
                        SPARK,
                        0.5,
                        "rover",
                        22.0,
                    )
                )
        if rover.mode == "dead":
            for _ in range(self._rate("smoke", 6, dt)):
                self.particles.append(
                    Particle(
                        rng.uniform(-3, 3),
                        3,
                        rng.uniform(-1, 2),
                        rng.uniform(2, 4),
                        "(",
                        (120, 120, 125),
                        2.2,
                        "rover",
                        -0.5,
                    )
                )
        self._step(dt)

    def _inside_dome(self, p: Particle) -> bool:
        """Is a screen-space particle inside the shield? (Hazards must not cross it.)"""
        if self.dome is None or p.space != "screen":
            return False
        cx, ground, rx, ry = self.dome
        up = (ground - 1) - p.y
        return up >= 0 and ((p.x - cx) / max(rx, 1)) ** 2 + (up / max(ry, 1)) ** 2 <= 1.0

    def _step(self, dt: float) -> None:
        alive = []
        for p in self.particles:
            p.age += dt
            if p.age >= p.ttl:
                continue
            if self._inside_dome(p) and p.color not in (CYAN, SPARK, GREEN):
                # a meteor or a puff of dust reached the shield: stop it at the edge with a spark
                self.shield_hit = max(self.shield_hit, 0.6)
                for _ in range(2):
                    self.particles_new.append(
                        Particle(
                            p.x,
                            p.y,
                            self.rng.uniform(-6, 6),
                            self.rng.uniform(-6, 4),
                            "*",
                            CYAN,
                            0.35,
                            "screen",
                        )
                    )
                continue
            p.x += p.vx * dt
            if p.space == "screen":
                p.y += p.vy * dt
            else:
                p.y += p.vy * dt
                p.vy -= p.gravity * dt
            if p.land and p.y <= 0:
                self.burst(p.x, 0.5, 5, ".,:", SAND, 4, 0.7)
                self.do_shake(0.12, 1)
                continue
            if p.space != "screen" and p.y < -2:
                continue
            alive.append(p)
        self.particles = alive + self.particles_new
        self.particles_new = []
        self.labels = [
            lb for lb in self.labels if (setattr(lb, "age", lb.age + dt) or True) and lb.age < lb.ttl
        ]
        self.flash = (self.flash[0], max(0.0, self.flash[1] - dt * 2.2))
        self.shake_t = max(0.0, self.shake_t - dt)
        self.comms_pulse = max(0.0, self.comms_pulse - dt)
        self.shield_hit = max(0.0, self.shield_hit - dt * 4)

    def shake_offset(self, t: float) -> tuple[int, int]:
        if self.shake_t <= 0:
            return 0, 0
        n = int(t * 40)
        return ((n * 7) % 3 - 1) * self.shake_amp, ((n * 5) % 3 - 1) * (1 if self.shake_amp else 0)
