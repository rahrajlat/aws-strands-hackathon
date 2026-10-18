"""Score the real Decider 2B against the sim's oracle on generated rover situations.

    uv run python scripts/eval_decider.py --n 120 --render prose
Needs a running server:  .venv-decider/bin/strands-decider serve <ckpt> --device cuda --port 8099
"""

from __future__ import annotations

import argparse
import random
import statistics

from roverthinking.decisions import questions as Q
from roverthinking.decisions.model import Decider2B
from roverthinking.decisions.render import as_json, as_prose
from roverthinking.decisions.triage import decide_instrument, decide_triage
from roverthinking.sim import oracle
from roverthinking.sim.hazards import ROCKS
from roverthinking.sim.rover import Rover

KINDS = ["dust_storm", "rockfall", "sand_trap", "steep_slope", "sensor_fault", "solar_flare", "meteor_shower"]


def situation(rng: random.Random):
    r = Rover(seed=rng.randrange(10**6))
    r.shield_charges = rng.choice([0, 1, 2, 2])
    r.wheels = rng.choice([30, 55, 65, 90, 100])
    r.nav = rng.choice([30, 55, 70, 100])
    h = r.start_hazard(rng.choice(KINDS), ahead=100, severity=rng.choice([1, 2, 3]))
    return r, h


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--render", choices=["json", "prose"], default="prose")
    ap.add_argument("--url", default="http://127.0.0.1:8099")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mode", choices=["single", "split"], default="split")
    a = ap.parse_args()
    dec = Decider2B(a.url, render=as_prose if a.render == "prose" else as_json)
    rng = random.Random(a.seed)
    rows = []
    for _ in range(a.n):
        r, h = situation(rng)
        if a.mode == "split":
            d = decide_triage(dec, r.snapshot(h), h.x is not None)[0]
        else:
            d = dec.decide(r.snapshot(h), Q.HAZARD_TRIAGE)
        rows.append(
            (
                h.kind,
                d.choice in oracle.acceptable_triage(r, h),
                d.confidence,
                d.latency_ms,
                d.choice,
                oracle.ideal_triage(r, h),
            )
        )
    acc = sum(x[1] for x in rows) / len(rows)
    print(
        f"[triage/{a.mode}/{a.render}] n={len(rows)} accuracy={acc:.2f} "
        f"median latency={statistics.median(x[3] for x in rows):.0f} ms"
    )
    for lo, hi in ((0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
        b = [x for x in rows if lo <= x[2] < hi]
        if b:
            print(f"  conf {lo:.1f}-{min(hi, 1):.1f}: n={len(b):3d} acc={sum(x[1] for x in b) / len(b):.2f}")
    by = {}
    for x in rows:
        by.setdefault(x[0], []).append(x[1])
    print("  by hazard:", {k: f"{sum(v) / len(v):.2f}" for k, v in by.items()})
    miss = {}
    for x in rows:
        if not x[1]:
            miss[(x[5], x[4])] = miss.get((x[5], x[4]), 0) + 1
    print("  top misses (ideal -> chose):", sorted(miss.items(), key=lambda kv: -kv[1])[:5])

    rocks = []
    for _ in range(40):
        r = Rover(seed=rng.randrange(10**6))
        h = r.start_hazard("strange_rock", ahead=100)
        h.rock_type = rng.choice(sorted(ROCKS))
        h.description = ROCKS[h.rock_type]
        if a.mode == "split":
            d = decide_instrument(dec, r.snapshot(h))[0]
        else:
            d = dec.decide(r.snapshot(h), Q.INSTRUMENT_CHOICE)
        rocks.append(
            (
                d.choice == oracle.ideal_instrument(h)
                or (d.choice == "spectrometer" and h.rock_type == "igneous"),
                d.confidence,
            )
        )
    print(
        f"[instrument/{a.mode}] n=40 accuracy={sum(x[0] for x in rocks) / 40:.2f} "
        f"mean conf={statistics.mean(x[1] for x in rocks):.2f}"
    )

    waits = []
    for _ in range(60):
        r, h = situation(rng)
        if rng.random() < 0.5:
            r.hazards.clear()
            h = r.start_hazard(
                rng.choice(["rockfall", "sand_trap", "steep_slope"]),
                ahead=100,
                severity=rng.choice([1, 2, 3]),
            )
        d = dec.decide(r.snapshot(h), Q.CAN_WAIT)
        waits.append((d.choice == ("yes" if oracle.can_wait(r, h) else "no"), d.confidence))
    print(
        f"[can_wait] n=60 accuracy={sum(x[0] for x in waits) / 60:.2f} "
        f"mean conf={statistics.mean(x[1] for x in waits):.2f}"
    )


if __name__ == "__main__":
    main()
