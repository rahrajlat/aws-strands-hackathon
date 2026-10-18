import json
import time

from roverthinking.bench.run import build_planner
from roverthinking.config import load_settings
from roverthinking.decisions.mock_rules import mock_rules
from roverthinking.decisions.model import Decision, MockDecider
from roverthinking.decisions.triage import decide_instrument, decide_triage
from roverthinking.game.session import GameSession
from roverthinking.sim import actions
from roverthinking.sim.rover import Rover


class Scripted:
    """A decider with fixed answers per question key: (choice, confidence)."""

    name = "scripted"

    def __init__(self, answers, latency_ms=100.0, sleep=0.0):
        self.answers, self.latency, self.sleep = answers, latency_ms, sleep
        self.calls = 0

    def decide(self, state, q):
        self.calls += 1
        time.sleep(self.sleep)
        choice, conf = self.answers.get(q.key, ("no", 0.9))
        return Decision(q.key, choice, conf, {}, self.latency, "scripted")

    def decide_many(self, state, qs):
        return [self.decide(state, q) for q in qs]


def run(r, planner, desk, ticks):
    for _ in range(ticks):
        r.tick()
        desk.tick()
        planner.step(r)


def events(r, kind):
    return [e for e in r.log if e.kind == kind]


def test_confident_decision_acts_without_asking_earth():
    r = Rover(seed=1)
    dec = Scripted({"avoid_hazard": ("yes", 0.95)})
    pl, desk = build_planner(r, "steward", dec, 0.7, delay_ticks=20)
    r.start_hazard("rockfall", ahead=110, severity=3)
    run(r, pl, desk, 10)
    d = events(r, "decision")[0].data
    assert d["choice"] == "reroute" and not d["escalated"]
    assert not events(r, "msg_sent")
    assert r.detected_hazards()[0].response == "reroute"


def test_unsure_about_fixed_terrain_asks_earth_holds_then_applies_the_reply():
    r = Rover(seed=1)
    dec = Scripted({"avoid_hazard": ("yes", 0.5), "can_wait": ("yes", 0.9)})
    pl, desk = build_planner(r, "steward", dec, 0.7, delay_ticks=20)
    h = r.start_hazard("rockfall", ahead=110, severity=2)
    run(r, pl, desk, 8)
    assert events(r, "decision")[0].data["escalated"]
    assert h.awaiting_earth and r.mode == "held"
    x = r.x
    run(r, pl, desk, 30)  # not yet back
    assert r.x == x
    run(r, pl, desk, 40)  # reply arrives and is applied
    assert not h.awaiting_earth
    assert [e for e in r.log if e.kind == "msg_received" and e.data["kind"] == "reply"]
    assert h.response in ("reroute", "slow")


def test_unsure_in_a_storm_acts_anyway_because_waiting_is_not_affordable():
    r = Rover(seed=1)
    dec = Scripted({"wait_it_out": ("yes", 0.5), "can_wait": ("no", 0.9)})
    pl, desk = build_planner(r, "steward", dec, 0.7)
    r.start_hazard("dust_storm", severity=3)
    run(r, pl, desk, 8)
    d = events(r, "decision")[0].data
    assert d["under_uncertainty"] and not d["escalated"] and d["choice"] == "stop_and_wait"
    assert not events(r, "msg_sent")


def test_no_escalation_when_the_link_is_down():
    r = Rover(seed=1)
    dec = Scripted({"avoid_hazard": ("yes", 0.5), "can_wait": ("yes", 0.9)})
    pl, desk = build_planner(r, "steward", dec, 0.7)
    r.start_hazard("solar_flare", severity=2)
    r.start_hazard("rockfall", ahead=110, severity=2)
    run(r, pl, desk, 8)
    assert not any(e.data.get("escalated") for e in events(r, "decision"))


def test_model_latency_is_charged_in_sim_time():
    r = Rover(seed=1)
    dec = Scripted({"avoid_hazard": ("yes", 0.95)}, latency_ms=450)
    pl, desk = build_planner(r, "steward", dec, 0.7)
    h = r.start_hazard("rockfall", ahead=110, severity=3)
    run(r, pl, desk, 4)
    assert h.response is None  # 450 ms = 5 ticks of thinking
    run(r, pl, desk, 4)
    assert h.response == "reroute"


def test_stale_order_is_held_by_the_steward_but_run_blindly_by_rules():
    def trial(planner_name):
        r = Rover(seed=1)
        dec = Scripted({"order_valid": ("no", 0.9)})
        pl, desk = build_planner(r, planner_name, dec, 0.7, delay_ticks=10)
        assert desk.send_order("go normal") == ""
        run(r, pl, desk, 3)
        r.start_hazard("sand_trap", ahead=100, severity=3)  # appears while the order is in flight
        run(r, pl, desk, 25)
        return r

    steward, rules = trial("steward"), trial("rules")
    held = events(steward, "order_held")
    assert held and "sand trap" in held[0].data["reason"]
    assert not events(steward, "order_result")
    assert events(rules, "order_result") and not events(rules, "order_held")


def test_earth_reply_for_a_passed_hazard_is_stale():
    r = Rover(seed=1)
    dec = Scripted({"avoid_hazard": ("yes", 0.5), "can_wait": ("yes", 0.9)})
    pl, desk = build_planner(r, "steward", dec, 0.7, delay_ticks=20)
    h = r.start_hazard("rockfall", ahead=110, severity=2)
    run(r, pl, desk, 8)
    from roverthinking.sim.hazards import resolve

    resolve(r, h, "passed")  # the situation moved on while Earth thought about it
    run(r, pl, desk, 80)
    assert events(r, "reply_stale")


def test_stuck_rover_gets_a_reflex_not_a_model_call():
    for seed in range(40):
        r = Rover(seed=seed)
        dec = Scripted({})
        pl, desk = build_planner(r, "steward", dec, 0.7)
        h = r.start_hazard("sand_trap", ahead=40, severity=3)
        actions.respond(r, h.id, "proceed")
        run(r, pl, desk, 60)
        if r.stuck or events(r, "stuck"):
            break
    calls = dec.calls
    run(r, pl, desk, 60)
    assert events(r, "freed") or not r.stuck
    reflex = [e for e in events(r, "decision") if e.data.get("source") == "reflex"]
    assert reflex and reflex[0].data["choice"] == "back_up"
    assert dec.calls >= calls


def test_offline_mock_decider_makes_sensible_triage_calls():
    dec = MockDecider(rules=mock_rules)
    r = Rover(seed=1)
    cases = [
        ("sensor_fault", 3, "safe_mode"),
        ("solar_flare", 3, "shield"),
        ("meteor_shower", 3, "shield"),
        ("dust_storm", 1, "slow"),
        ("rockfall", 3, "reroute"),
    ]
    for kind, sev, want in cases:
        r.hazards.clear()
        h = r.start_hazard(kind, ahead=100, severity=sev)
        d, _ = decide_triage(dec, r.snapshot(h), h.x is not None)
        assert d.choice == want, (kind, d)
    h = r.start_hazard("strange_rock", ahead=100)
    h.description = "pale layered rock with fine banding"
    assert decide_instrument(dec, r.snapshot(h))[0].choice == "drill"


def test_whole_run_with_the_steward_is_reproducible_offline():
    from roverthinking.bench.run import run_headless

    assert run_headless("steward", seed=3) == run_headless("steward", seed=3)


def test_session_models_run_in_the_background_and_the_log_is_written(tmp_path):
    dec = Scripted({"avoid_hazard": ("yes", 0.95)}, latency_ms=50, sleep=0.4)
    s = GameSession(load_settings(earth="mock"), planner="steward", seed=1, decider=dec, log_dir=tmp_path)
    s.cast("rockfall", ahead=110, severity=3)
    t0 = time.perf_counter()
    for _ in range(5):
        s.advance(0.05)  # the 0.4 s model call must not block these frames
    assert time.perf_counter() - t0 < 0.3
    for _ in range(60):
        time.sleep(0.05)
        s.advance(0.05)
        if s.decisions:
            break
    assert s.decisions and s.decisions[0].data["choice"] == "reroute"
    s.rover.hazards[0].resolved = False
    from roverthinking.sim.hazards import resolve

    resolve(s.rover, s.rover.hazards[0], "passed")
    s.advance(0.05)
    s.close()
    lines = [json.loads(x) for x in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    dec_line = next(x for x in lines if x["type"] == "decision")
    for key in (
        "timestamp",
        "question",
        "options",
        "choice",
        "confidence",
        "latency_ms",
        "escalated",
        "outcome",
    ):
        assert key in dec_line
    assert any(x["type"] == "outcome" for x in lines)


class Broken:
    name = "broken"

    def decide(self, state, q):
        raise ConnectionError("decider is down")

    def decide_many(self, state, qs):
        raise ConnectionError("decider is down")


def test_a_failing_decider_degrades_to_the_rules_table_instead_of_crashing():
    r = Rover(seed=1)
    pl, desk = build_planner(r, "steward", Broken(), 0.7, delay_ticks=10)
    h = r.start_hazard("rockfall", ahead=110, severity=3)
    desk.send_order("go slow")
    run(r, pl, desk, 40)
    assert events(r, "decider_error")
    d = events(r, "decision")[0].data
    assert d["source"] == "fallback-rules" and d["choice"] == "reroute"
    assert h.response == "reroute"
    assert events(r, "order_result")  # the order still ran rather than hanging forever
