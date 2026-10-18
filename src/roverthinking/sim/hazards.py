"""Things that go wrong, and the rules for what they do to the rover."""

from __future__ import annotations

from roverthinking.sim.rover import (
    DETECT_RANGE,
    Alarm,
    Hazard,
    Job,
    Rover,
)

HAZARD_KINDS = (
    "dust_storm",
    "rockfall",
    "sand_trap",
    "steep_slope",
    "sensor_fault",
    "solar_flare",
    "meteor_shower",
    "strange_rock",
)
POSITIONAL = {"rockfall", "sand_trap", "steep_slope", "strange_rock"}
BYPASSABLE = {"rockfall", "sand_trap", "steep_slope"}
TIMED = {"dust_storm", "solar_flare", "meteor_shower", "sensor_fault"}
SHIELDABLE = {"dust_storm", "solar_flare", "meteor_shower"}
LENGTH = {
    "rockfall": (8, 14),
    "sand_trap": (25, 50),
    "steep_slope": (25, 45),
    "strange_rock": (0, 0),
}

ROCKS = {
    "sedimentary": "pale layered rock with fine banding, about 40 cm across",
    "igneous": "dark fine-grained basalt with small gas pockets, about 60 cm across",
    "ice": "frost-dusted lump of ice-rich regolith, slightly glistening",
    "clay": "reddish mud-cracked clay-rich outcrop with a smooth surface",
    "metallic": "rust-brown dense nodule that deflects the rover's magnet reading",
}
# science points by (rock type, instrument); skip scores nothing
SCIENCE = {
    "sedimentary": {"camera": 1, "spectrometer": 3, "drill": 5},
    "igneous": {"camera": 1, "spectrometer": 4, "drill": 2},
    "ice": {"camera": 2, "spectrometer": 5, "drill": 0},
    "clay": {"camera": 1, "spectrometer": 2, "drill": 5},
    "metallic": {"camera": 2, "spectrometer": 5, "drill": 0},
}
# instrument damage when the wrong tool meets the wrong rock
INSTRUMENT_DAMAGE = {("ice", "drill"): 30.0, ("metallic", "drill"): 20.0}
SAMPLE_TICKS = {"camera": 8, "spectrometer": 15, "drill": 40}


def spawn(r: Rover, kind: str, ahead: float | None = None, severity: int | None = None) -> Hazard:
    if kind not in HAZARD_KINDS:
        raise ValueError(f"unknown hazard {kind!r}; use one of {HAZARD_KINDS}")
    sev = severity if severity is not None else r.rng.choice((1, 1, 2, 2, 3))
    if sev not in (1, 2, 3):
        raise ValueError("severity must be 1, 2 or 3")
    x = length = None
    if kind in POSITIONAL:
        lo, hi = LENGTH[kind]
        length = float(r.rng.randint(lo, hi)) if hi else 0.0
        dist = ahead if ahead is not None else r.rng.uniform(90, 125)
        x = r.x + max(10.0, dist)
    duration = None
    if kind == "dust_storm":
        duration = 150 + 150 * sev
    elif kind == "solar_flare":
        duration = 60 + 40 * sev
    elif kind == "meteor_shower":
        duration = 80 + 40 * sev
    elif kind == "sensor_fault":
        duration = 500
    h = Hazard(r._hid, kind, sev, x, length or 0.0, r.tick_n, duration)
    r._hid += 1
    if kind == "strange_rock":
        h.rock_type = r.rng.choice(sorted(ROCKS))
        h.description = ROCKS[h.rock_type]
    r.hazards.append(h)
    r.emit(
        "hazard_start",
        id=h.id,
        hazard=kind,
        severity=sev,
        x=None if x is None else round(x),
        length=h.length,
    )
    if x is None or x - r.x <= DETECT_RANGE:
        _detect(r, h)
    return h


def _detect(r: Rover, h: Hazard) -> None:
    h.detected = True
    raise_alarm(r, h, "detected")


def raise_alarm(r: Rover, h: Hazard, reason: str) -> Alarm:
    for a in r.alarms:
        if a.hazard_id == h.id and a.state != "resolved":
            a.state, a.reason, a.severity, a.tick = "new", reason, h.severity, r.tick_n
            r.emit("alarm", id=a.id, hazard=h.kind, hazard_id=h.id, severity=h.severity, reason=reason)
            return a
    a = Alarm(r._aid, h.id, h.kind, h.severity, r.tick_n, reason)
    r._aid += 1
    r.alarms.append(a)
    r.emit("alarm", id=a.id, hazard=h.kind, hazard_id=h.id, severity=h.severity, reason=reason)
    return a


def resolve(r: Rover, h: Hazard, how: str = "ended") -> None:
    h.resolved = True
    h.awaiting_earth = False
    for a in r.alarms:
        if a.hazard_id == h.id:
            a.state = "resolved"
    r.emit("hazard_end", id=h.id, hazard=h.kind, how=how)


def apply(r: Rover, h: Hazard) -> None:
    """Per-tick global effects of a hazard, detection, and timed expiry."""
    if h.x is not None and not h.detected and h.x - r.x <= DETECT_RANGE:
        _detect(r, h)
    if h.duration is not None and r.tick_n - h.start_tick >= h.duration:
        resolve(r, h)
        return
    sev = h.severity
    if h.kind == "dust_storm":
        r.visibility = min(r.visibility, {1: 0.55, 2: 0.3, 3: 0.08}[sev])
        if r.velocity >= 2.0 and r.rng.random() < 0.004 * sev:
            r.damage("wheels", 4.0 * sev, "unseen rocks in the dust")
        if sev == 3 and not r.shielded and r.rng.random() < 0.03:
            r.damage("instruments", 1.5, "abrasive dust")
    elif h.kind == "solar_flare":
        r.flare_active = True
        if not r.safe and not r.shielded and r.rng.random() < 0.02 * sev:
            r.damage("instruments", 2.0 * sev, "radiation")
    elif h.kind == "meteor_shower":
        if r.rng.random() < 0.04 * sev:
            if r.shielded:
                r.emit("impact", blocked=True)
            else:
                r.emit("impact", blocked=False)
                part = "wheels" if r.rng.random() < 0.6 else "instruments"
                r.damage(part, 2.0 * sev, "meteor impact")
    elif h.kind == "sensor_fault":
        r.nav = max(10.0, r.nav - 0.02 * sev) if not r.safe else r.nav
        if r.velocity >= 2.0 and r.rng.random() < 0.006 * sev:
            r.damage("wheels", 5.0 * sev, "drove blind")
        if r.safe and r.tick_n >= r.safe_until:
            r.nav = 100.0
            resolve(r, h, "rebooted")


def zone_tick(r: Rover, h: Hazard, v: float) -> None:
    """Effects of being on/at a positional hazard, and resolving it once passed."""
    assert h.x is not None
    if h.kind == "strange_rock":
        if r.x >= h.x:
            if h.plan and h.plan != "skip" and r.instruments > 0:
                r.job = Job("sample", r.tick_n + SAMPLE_TICKS[h.plan], h.id, h.plan)
                r.emit("sampling", hazard_id=h.id, instrument=h.plan, rock=h.rock_type)
                resolve(r, h, "sampling")
            else:
                resolve(r, h, "passed")
        return
    end = h.x + max(h.length, 1.0)
    if r.x >= end:
        resolve(r, h, "passed")
        return
    if not (h.x <= r.x < end) or v == 0:
        return
    sev, fast = h.severity, v >= 2.0
    if h.bypass:
        return
    if h.kind == "rockfall":
        r.damage("wheels", (1.2 * sev if fast else 0.3 * sev) * v, "boulders")
        if sev == 3 and fast and r.rng.random() < 0.06:
            _get_stuck(r, h, "wedged between boulders")
    elif h.kind == "sand_trap":
        r.wheels = max(0.0, r.wheels - 0.05 * v)
        if r.rng.random() < (0.03 * sev if fast else 0.004 * sev):
            _get_stuck(r, h, "bogged in soft sand")
    elif h.kind == "steep_slope":
        if fast and sev >= 2:
            r.damage("wheels", 0.1 * sev, "steep slope")
        if sev == 3 and r.rng.random() < (0.015 if fast else 0.001):
            r._die("tipped over on a steep slope")


def _get_stuck(r: Rover, h: Hazard, why: str) -> None:
    if not r.stuck:
        r.stuck, r.stuck_by = True, h.id
        r.emit("stuck", why=why, hazard_id=h.id)
        raise_alarm(r, h, "stuck")


def finish_sample(r: Rover, j: Job) -> None:
    h = r.hazard(j.hazard_id) if j.hazard_id is not None else None
    rock = h.rock_type if h else ""
    inst = j.instrument or "camera"
    points = SCIENCE.get(rock, {}).get(inst, 0)
    bad = INSTRUMENT_DAMAGE.get((rock, inst), 0.0)
    if bad:
        r.damage("instruments", bad, f"{inst} on {rock}")
    r.science += points
    if points:
        r.samples += 1
    r.emit("sample_done", instrument=inst, rock=rock, points=points)


def best_instrument(rock_type: str) -> str:
    table = SCIENCE.get(rock_type, {})
    return max(table, key=lambda k: table[k]) if table else "camera"
