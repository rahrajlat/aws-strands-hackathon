"""Scenarios: single hazards, wizard choices, cascades, and YAML save/load."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from roverthinking.sim.hazards import HAZARD_KINDS, POSITIONAL

SEVERITY_NAMES = {1: "minor", 2: "major", 3: "catastrophic"}
HAZARD_LABELS = {
    "dust_storm": "Dust storm",
    "rockfall": "Rockfall",
    "sand_trap": "Sand trap",
    "steep_slope": "Steep slope",
    "sensor_fault": "Sensor fault",
    "solar_flare": "Solar flare",
    "meteor_shower": "Meteor shower",
    "strange_rock": "Strange rock",
}
QUICK_KEYS = {
    "d": "dust_storm",
    "r": "rockfall",
    "t": "sand_trap",
    "h": "steep_slope",
    "m": "meteor_shower",
    "n": "sensor_fault",
    "f": "solar_flare",
    "g": "strange_rock",
}
AHEAD_CHOICES = {"close (60 m)": 60.0, "medium (110 m)": 110.0, "far (250 m)": 250.0}
SCENARIO_DIR = Path("scenarios")


@dataclass
class ScenarioEvent:
    at_tick: int  # ticks after the scenario starts; 10 ticks = 1 second at 1x
    kind: str
    ahead: float | None = None  # metres ahead of the rover (positional hazards only)
    severity: int = 2

    def validate(self) -> None:
        if self.kind not in HAZARD_KINDS:
            raise ValueError(f"unknown hazard {self.kind!r}")
        if self.severity not in SEVERITY_NAMES:
            raise ValueError("severity must be 1, 2 or 3")
        if self.at_tick < 0:
            raise ValueError("at_tick must be >= 0")
        if self.ahead is not None and (self.ahead < 10 or self.ahead > 600):
            raise ValueError("ahead must be between 10 and 600 metres")

    def describe(self) -> str:
        where = ""
        if self.kind in POSITIONAL:
            where = f" {int(self.ahead)} m ahead" if self.ahead else " ahead"
        return f"{SEVERITY_NAMES[self.severity].title()} {HAZARD_LABELS[self.kind].lower()}{where}"


@dataclass
class Scenario:
    name: str
    seed: int = 0
    description: str = ""
    events: list[ScenarioEvent] = field(default_factory=list)

    def validate(self) -> None:
        for e in self.events:
            e.validate()


def save_scenario(sc: Scenario, path: Path | None = None) -> Path:
    sc.validate()
    path = path or SCENARIO_DIR / f"{sc.name.lower().replace(' ', '-')}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "name": sc.name,
        "seed": sc.seed,
        "description": sc.description,
        "events": [
            {"at_tick": e.at_tick, "kind": e.kind, "ahead": e.ahead, "severity": e.severity}
            for e in sc.events
        ],
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def load_scenario(path: Path) -> Scenario:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict) or "events" not in data:
        raise ValueError(f"{path}: a scenario needs a name and an events list")
    sc = Scenario(
        name=str(data.get("name", Path(path).stem)),
        seed=int(data.get("seed", 0)),
        description=str(data.get("description", "")),
        events=[
            ScenarioEvent(int(e["at_tick"]), e["kind"], e.get("ahead"), int(e.get("severity", 2)))
            for e in data["events"]
        ],
    )
    sc.validate()
    return sc


CASCADES = [
    # a storm blinds the rover, then a sensor fault, then a trap in poor visibility
    [("dust_storm", None, 3, 0), ("sensor_fault", None, 2, 120), ("sand_trap", 110.0, 2, 260)],
    # a meteor shower, then a rockfall shakes loose on a slope
    [("meteor_shower", None, 3, 0), ("steep_slope", 100.0, 2, 150), ("rockfall", 110.0, 3, 300)],
    # a flare blinds comms, then a rockfall on a slope
    [("solar_flare", None, 2, 0), ("steep_slope", 100.0, 3, 80), ("rockfall", 110.0, 2, 200)],
    # sensors fail just before a boulder field
    [("sensor_fault", None, 2, 0), ("rockfall", 120.0, 3, 60), ("strange_rock", 150.0, 2, 220)],
]


def random_cascade(rng: random.Random) -> Scenario:
    chain = rng.choice(CASCADES)
    events = [ScenarioEvent(delay, kind, ahead, sev) for kind, ahead, sev, delay in chain]
    return Scenario("cascade", events=events, description="A chain of three linked hazards.")


SEVERITY_WORDS = {"minor": 1, "small": 1, "major": 2, "medium": 2, "catastrophic": 3, "severe": 3}


def parse_add(text: str) -> ScenarioEvent:
    """'/add rockfall 3 80' / 'dust storm major' / 'meteor shower catastrophic' -> an event.

    Words: a hazard (any unambiguous prefix of its name), a severity (1-3 or minor/major/
    catastrophic) and, for terrain hazards, a distance ahead in metres (10 or more).
    """
    words = text.lower().replace("_", " ").split()
    sev, ahead, name = 2, None, []
    for w in words:
        if w in SEVERITY_WORDS:
            sev = SEVERITY_WORDS[w]
        elif w.isdigit() and int(w) in (1, 2, 3):
            sev = int(w)
        elif w.isdigit():
            ahead = float(w)
        else:
            name.append(w)
    key = " ".join(name)
    if not key:
        raise ValueError("add what? e.g. /add rockfall 3 80  (hazards: " + ", ".join(HAZARD_LABELS) + ")")
    hits = [
        k
        for k, label in HAZARD_LABELS.items()
        if k.replace("_", " ").startswith(key) or label.lower().startswith(key) or key in k.replace("_", " ")
    ]
    if len(hits) != 1:
        raise ValueError(f"{key!r} matches {hits or 'nothing'}; hazards: {', '.join(HAZARD_LABELS)}")
    ev = ScenarioEvent(0, hits[0], ahead if hits[0] in POSITIONAL else None, sev)
    ev.validate()
    return ev
