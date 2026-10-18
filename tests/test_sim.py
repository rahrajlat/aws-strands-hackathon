import ast
import pathlib

import pytest

from roverthinking.bench.run import run_headless, run_rover
from roverthinking.earth.mission_control import OrderError, parse_order
from roverthinking.game.scenarios import parse_add
from roverthinking.planners import make_planner
from roverthinking.sim import actions, oracle
from roverthinking.sim.hazards import HAZARD_KINDS, SCIENCE, best_instrument
from roverthinking.sim.rover import GOAL_M, Rover


def run(r: Rover, ticks: int, planner=None) -> None:
    for _ in range(ticks):
        r.tick()
        if planner:
            planner.step(r)


def test_quiet_traverse_reaches_the_goal():
    r = Rover(seed=1)
    run(r, 1200)
    assert r.mode == "done" and r.x >= GOAL_M and r.wheels == 100


def test_seeded_run_is_reproducible():
    a = run_headless("rules", seed=7)
    assert a == run_headless("rules", seed=7)
    assert a != run_headless("rules", seed=8)


def test_event_log_identical_for_same_seed():
    def log(seed):
        r = Rover(seed=seed, random_rate=1 / 100)
        run(r, 800, make_planner("rules"))
        return [(e.tick, e.kind, tuple(sorted(e.data.items(), key=str))) for e in r.log]

    assert log(3) == log(3)


def test_driving_into_boulders_hurts_but_rerouting_does_not():
    def trial(choice):
        r = Rover(seed=1)
        h = r.start_hazard("rockfall", ahead=100, severity=2)
        if choice:
            assert actions.respond(r, h.id, choice).ok
        run(r, 120)
        return r.wheels

    assert trial(None) < 90  # default: keeps driving at full speed
    assert trial("slow") > trial(None)
    assert trial("reroute") == 100


def test_unhandled_sand_trap_strands_the_rover_and_back_up_frees_it():
    for seed in range(40):
        r = Rover(seed=seed)
        r.start_hazard("sand_trap", ahead=60, severity=3)
        run(r, 80)
        if r.stuck:
            break
    else:
        pytest.fail("no seed got stuck")
    assert r.mode == "stuck"
    for _ in range(5):
        if not r.stuck:
            break
        assert actions.order_back_up(r).ok
        run(r, 30)
    assert not r.stuck


def test_stop_and_wait_holds_and_stalls_raise_a_new_alarm():
    r = Rover(seed=1)
    h = r.start_hazard("rockfall", ahead=100, severity=2)
    actions.respond(r, h.id, "stop_and_wait")
    x = r.x
    run(r, 100)
    assert r.x == x and r.mode == "held"
    run(r, 100)
    assert [a for a in r.pending_alarms() if a.reason == "stalled"]


def test_sampling_scores_by_rock_and_the_wrong_tool_damages():
    def sample(rock, tool):
        r = Rover(seed=1)
        h = r.start_hazard("strange_rock", ahead=40)
        h.rock_type = rock
        assert actions.sample(r, h.id, tool).ok
        run(r, 150)
        return r.science, r.instruments

    assert sample("sedimentary", "drill")[0] == 5
    assert sample("ice", "drill") == (0, 70.0)
    assert best_instrument("ice") == "spectrometer"
    assert all(SCIENCE[rock][best_instrument(rock)] == max(SCIENCE[rock].values()) for rock in SCIENCE)


@pytest.mark.parametrize("kind", HAZARD_KINDS)
def test_every_hazard_kind_runs_under_the_rules_planner(kind):
    r = Rover(seed=2)
    r.start_hazard(kind)
    run(r, 300, make_planner("rules"))


def test_rules_planner_beats_doing_nothing_on_a_busy_traverse():
    def go(planner):
        r = Rover(seed=5, random_rate=1 / 150)
        return run_rover(r, planner(), 3000).summary.score

    class Nothing:
        name = "none"

        def step(self, r):
            return []

    assert go(lambda: make_planner("rules")) > go(Nothing)


def test_sim_has_no_ui_imports():
    root = pathlib.Path(__file__).parents[1] / "src/roverthinking/sim"
    banned = {"textual", "rich", "roverthinking.tui", "roverthinking.planners"}
    for f in root.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert not any(m == b or m.startswith(b + ".") for b in banned), (f.name, m)


def test_severe_dust_wears_instruments_unless_the_shield_is_up():
    def trial(shield):
        r = Rover(seed=1)
        h = r.start_hazard("dust_storm", severity=3)
        actions.respond(r, h.id, "shield" if shield else "stop_and_wait")
        run(r, 500)
        return r.instruments

    assert trial(False) < 95
    assert trial(True) == 100


def test_safe_mode_clears_a_sensor_fault():
    r = Rover(seed=1)
    h = r.start_hazard("sensor_fault", severity=3)
    assert actions.respond(r, h.id, "safe_mode").ok
    run(r, 60)
    assert h.resolved and not r.safe and r.nav == 100


def test_shield_takes_time_to_raise_blocks_meteors_and_has_two_charges():
    r = Rover(seed=1)
    h = r.start_hazard("meteor_shower", severity=3)
    assert actions.respond(r, h.id, "shield").ok
    assert r.shield == "deploying" and r.shield_charges == 1
    run(r, 3)
    assert not r.shielded and r.velocity == 0  # stationary and unprotected while raising
    run(r, 6)
    assert r.shielded and r.mode == "shielded"
    wheels, instruments = r.wheels, r.instruments  # a hit while it was still raising is fair
    run(r, 200)
    assert (r.wheels, r.instruments) == (wheels, instruments)  # nothing gets through now
    assert [e for e in r.log if e.kind == "impact" and e.data["blocked"]]
    run(r, 120)  # the shower ends, the shield retracts, the rover drives again
    assert r.shield == "off" and r.mode == "driving"
    for _ in range(2):  # the second (last) charge, then it is spent
        h = r.start_hazard("solar_flare", severity=3)
        res = actions.respond(r, h.id, "shield")
        if not res.ok:
            break
        run(r, 400)
    assert r.shield_charges == 0
    h = r.start_hazard("meteor_shower", severity=3)
    refused = actions.respond(r, h.id, "shield")
    assert not refused.ok and "charges" in refused.reason


def test_meteors_hurt_an_unshielded_rover():
    r = Rover(seed=1)
    h = r.start_hazard("meteor_shower", severity=3)
    actions.respond(r, h.id, "stop_and_wait")
    run(r, 250)
    assert r.damage_taken > 0


def test_shield_is_only_for_external_threats():
    r = Rover(seed=1)
    h = r.start_hazard("rockfall", ahead=100, severity=3)
    res = actions.respond(r, h.id, "shield")
    assert not res.ok and "dust storms" in res.reason and r.shield_charges == 2


def test_player_can_remove_hazards_even_mid_jam():
    r = Rover(seed=1)
    h = r.start_hazard("sand_trap", ahead=60, severity=3)
    r.stuck, r.stuck_by = True, h.id
    assert r.remove_hazard(h.id)
    assert h.resolved and not r.stuck
    assert not r.remove_hazard(h.id) and not r.remove_hazard(999)
    assert [e for e in r.log if e.kind == "hazard_end" and e.data["how"] == "removed"]
    assert all(a.state == "resolved" for a in r.alarms if a.hazard_id == h.id)
    r.schedule_hazard(500, "dust_storm")
    assert r.cancel_scheduled(0) and not r.schedule


def test_clearing_the_session_removes_active_and_scheduled_events():
    from roverthinking.config import load_settings
    from roverthinking.game.session import GameSession

    s = GameSession(load_settings(earth="mock"), seed=1)
    s.cast("dust_storm")
    s.cast("rockfall", ahead=90)
    s.rover.schedule_hazard(900, "solar_flare")
    assert s.clear_hazards() == 3
    assert not s.rover.active_hazards() and not s.rover.schedule


def test_add_command_parser():
    assert parse_add("rockfall 3 80").severity == 3 and parse_add("rockfall 3 80").ahead == 80
    assert parse_add("dust storm major").severity == 2
    assert parse_add("meteor catastrophic").kind == "meteor_shower"
    assert parse_add("flare").kind == "solar_flare"
    for bad in ("", "banana", "s"):  # 's' is ambiguous (sand, slope, sensor, solar)
        with pytest.raises(ValueError):
            parse_add(bad)


def test_actions_reject_invalid_orders_with_reason():
    r = Rover(seed=1)
    h = r.start_hazard("dust_storm", severity=1)
    cases = [
        actions.respond(r, 99, "slow"),
        actions.respond(r, h.id, "teleport"),
        actions.respond(r, h.id, "reroute"),  # cannot reroute a storm
        actions.respond(r, h.id, "back_up"),  # not stuck
        actions.sample(r, h.id, "drill"),  # not a rock
        actions.order_unshield(r),  # nothing to lower
        actions.order_back_up(r),
        actions.dispatch(r, "launch", x=1),
    ]
    assert not any(c.ok for c in cases)
    assert all(c.reason for c in cases)


def test_dead_rover_rejects_everything():
    r = Rover(seed=1)
    r._die("test")
    assert r.mode == "dead"
    assert not actions.order_drive(r).ok and not actions.order_shield(r).ok


def test_oracle_saves_the_shield_for_severe_external_threats():
    r = Rover(seed=1)
    storm = r.start_hazard("dust_storm", severity=1)
    assert oracle.ideal_triage(r, storm) == "slow"
    storm.severity = 3
    assert oracle.ideal_triage(r, storm) == "shield"
    r.shield_charges = 0
    assert oracle.ideal_triage(r, storm) == "stop_and_wait"
    r.shield_charges = 2
    flare = r.start_hazard("solar_flare", severity=3)
    assert oracle.ideal_triage(r, flare) == "shield"
    rock = r.start_hazard("rockfall", ahead=100, severity=3)
    assert oracle.ideal_triage(r, rock) == "reroute"


def test_order_grammar():
    assert parse_order("> go slow").args == {"speed": "slow"}
    assert parse_order("safe mode").action == "safe_mode"
    assert parse_order("shield").action == "shield"
    assert parse_order("shield down").action == "unshield"
    assert parse_order("sample drill").args == {"instrument": "drill"}
    for bad in ("", "dance", "sample banana", "ration moon"):
        with pytest.raises(OrderError):
            parse_order(bad)


def test_nothing_hurts_a_rover_whose_shield_is_up():
    r = Rover(seed=1)
    h = r.start_hazard("meteor_shower", severity=3)
    assert actions.respond(r, h.id, "shield").ok
    run(r, 12)
    assert r.shielded
    wheels, instruments, taken = r.wheels, r.instruments, r.damage_taken
    r.damage("wheels", 40, "anything at all")  # even a direct hit is refused
    r.damage("instruments", 40, "anything at all")
    assert (r.wheels, r.instruments, r.damage_taken) == (wheels, instruments, taken)
    run(r, 200)
    assert (r.wheels, r.instruments) == (wheels, instruments)
