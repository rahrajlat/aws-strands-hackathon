import pytest
from test_steward import Scripted, events, run

from roverthinking.bench.run import build_planner
from roverthinking.config import Settings, load_settings
from roverthinking.earth import providers as P
from roverthinking.earth.mission_control import OrderError, order_from_plan
from roverthinking.earth.schemas import (
    EscalationAnswer,
    OrderPlan,
    ScenarioEventPlan,
    ScenarioPlan,
)
from roverthinking.game.session import GameSession
from roverthinking.sim.rover import Rover
from roverthinking.tui.widgets.decision_panel import DecisionPanel, format_card, new_card


def test_mock_earth_turns_keywords_into_a_scenario():
    plan = P.MockEarth().plan_scenario("A catastrophic meteor shower hits, then extreme dust and a sand trap")
    assert [e.kind for e in plan.events] == ["meteor_shower", "dust_storm", "sand_trap"]
    assert plan.events[0].severity == 3 and plan.events[1].severity == 3
    assert plan.events[2].ahead_m == 110.0 and plan.events[1].ahead_m is None
    with pytest.raises(ValueError):
        P.MockEarth().plan_scenario("nothing dangerous here")


class FakeStrandsEarth(P.StrandsEarth):
    """A StrandsEarth whose model call is scripted, so no network or model is needed."""

    def __init__(self, answer=None, error=None):
        super().__init__(model=None, name="fake", model_id="fake-1")
        self.answer, self.error = answer, error

    def _ask(self, system, prompt, schema):
        if self.error:
            raise self.error
        return self.answer, 120.0, 500, 40


OPTIONS = {"slow": "crawl", "reroute": "go around", "stop_and_wait": "wait"}
SNAP = Rover().snapshot()


def test_strands_earth_returns_a_validated_recommendation_with_usage():
    ans = EscalationAnswer(recommendation="Reroute", reasoning="boulders", message="Go around.")
    reply = FakeStrandsEarth(ans).answer_escalation(SNAP, "hazard_triage", OPTIONS, "slow", 0.5)
    assert (reply.recommendation, reply.tokens_in, reply.tokens_out) == ("reroute", 500, 40)
    assert reply.message == "Go around."


def test_strands_earth_rejects_an_answer_that_is_not_an_option_and_falls_back():
    ans = EscalationAnswer(recommendation="fly away", reasoning="?", message="?")
    reply = FakeStrandsEarth(ans).answer_escalation(SNAP, "hazard_triage", OPTIONS, "slow", 0.5)
    assert "offline answer" in reply.reasoning  # the mock answered instead


def test_strands_earth_never_crashes_the_game_when_the_model_fails():
    reply = FakeStrandsEarth(error=ConnectionError("boom")).answer_escalation(
        SNAP, "hazard_triage", OPTIONS, "slow", 0.5
    )
    assert reply.recommendation and "boom" in reply.reasoning


def test_make_earth_falls_back_to_mock_with_a_clear_reason(monkeypatch):
    s = load_settings(earth="mock")
    assert P.make_earth(s)[0].name == "mock"
    dead = Settings(
        earth_provider="ollama", earth_model="gpt-oss:120b-cloud", ollama_host="http://127.0.0.1:9"
    )
    earth, note = P.make_earth(dead)
    assert earth.name == "mock" and "not reachable" in note
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nonexistent")
    earth, note = P.make_earth(Settings(earth_provider="bedrock", earth_model="m", aws_region="us-east-1"))
    assert earth.name == "mock" and "AWS credentials" in note


def test_ollama_means_cloud_models_only():
    local = Settings(earth_provider="ollama", earth_model="llama3.1:latest")
    earth, note = P.make_earth(local)
    assert earth.name == "mock" and "local model" in note and "ollama signin" in note
    direct = Settings(earth_provider="ollama", earth_model="gpt-oss:120b", ollama_host="https://ollama.com")
    assert P.is_ollama_cloud(direct) and "OLLAMA_API_KEY" in P.make_earth(direct)[1]
    assert P.is_ollama_cloud(Settings(earth_provider="ollama", earth_model="gpt-oss:120b-cloud"))


def test_bedrock_default_model_comes_from_config(monkeypatch):
    monkeypatch.delenv("ROVER_EARTH_MODEL", raising=False)
    assert load_settings(earth="bedrock").earth_model == "anthropic.claude-haiku-5-5"


def test_a_free_text_order_reduces_to_one_supported_command():
    plan = OrderPlan(understood=True, action="drive", speed="slow", explanation="slow down")
    order = order_from_plan(plan)
    assert (order.action, order.args) == ("drive", {"speed": "slow"})
    with pytest.raises(OrderError):
        order_from_plan(OrderPlan(understood=False, action="none", explanation="not an order"))


class LanguageEarth(P.MockEarth):
    name, model_id, interprets_text = "fake", "fake-1", True

    def parse_order(self, text, snapshot):
        return OrderPlan(understood=True, action="drive", speed="slow", explanation="ok")

    def plan_scenario(self, text):
        return ScenarioPlan(
            title="Test",
            description="d",
            events=[
                ScenarioEventPlan(kind="rockfall", severity=3, ahead_m=5000, after_seconds=2),
                ScenarioEventPlan(kind="dust_storm", severity=2, ahead_m=80, after_seconds=9),
            ],
        )


def pump(s, seconds):
    for _ in range(int(seconds / 0.05)):
        s.advance(0.05)


def test_session_reads_a_free_text_order_in_the_background_then_sends_it():
    import time

    s = GameSession(load_settings(earth="mock"), seed=1, earth=LanguageEarth())
    assert s.send_order("please ease off the throttle a little") == ""
    for _ in range(100):
        time.sleep(0.02)
        s.advance(0.05)
        if s.link.in_flight():
            break
    kinds = [e.kind for e in s.rover.log]
    assert "order_interpreting" in kinds and "order_interpreted" in kinds
    assert [m.text for m in s.link.in_flight()] == ["drive slow (interpreted)"]
    s.close()


def test_mock_earth_does_not_interpret_so_bad_orders_are_rejected_immediately():
    s = GameSession(load_settings(earth="mock"), seed=1)
    assert "not understood" in s.send_order("please ease off")
    s.close()


def test_session_scenario_plan_is_clamped_validated_and_scheduled():
    import time

    s = GameSession(load_settings(earth="mock"), seed=1, earth=LanguageEarth())
    s.plan_scenario("a rockfall then a storm")
    for _ in range(100):
        time.sleep(0.02)
        s.advance(0.05)
        if s.ready_plan:
            break
    assert s.ready_plan.title == "Test"
    sc = s.schedule_plan(s.ready_plan)
    assert sc.events[0].ahead == 400.0 and sc.events[1].ahead is None  # clamped / dropped
    assert len(s.rover.schedule) == 2
    s.close()


def test_llm_only_asks_earth_for_everything_and_waits_unprotected():
    def damage(planner_name):
        r = Rover(seed=2)
        pl, desk = build_planner(
            r,
            planner_name,
            Scripted({"is_external_threat": ("yes", 0.95), "is_severe": ("yes", 0.95)}),
            0.7,
            delay_ticks=60,
        )
        r.start_hazard("meteor_shower", severity=3)
        run(r, pl, desk, 240)
        return r.damage_taken, r, desk

    llm_dmg, llm_r, desk = damage("llm_only")
    stew_dmg, stew_r, _ = damage("steward")
    assert desk.earth_calls >= 1 and events(llm_r, "msg_sent")
    assert stew_r.shield_charges == 1  # the Steward shielded locally
    assert llm_dmg > stew_dmg  # waiting on Earth under meteors costs the rover


def test_llm_only_applies_earths_reply_after_the_round_trip():
    r = Rover(seed=1)
    pl, desk = build_planner(r, "llm_only", Scripted({}), 0.7, delay_ticks=20)
    h = r.start_hazard("rockfall", ahead=110, severity=3)
    run(r, pl, desk, 15)
    assert h.awaiting_earth and r.mode == "held"
    run(r, pl, desk, 60)
    assert h.response == "reroute" and not h.awaiting_earth
    d = events(r, "decision")[-1].data
    assert d["source"] == "earth" and d["reasoning"]


def test_every_decision_leaves_a_question_decision_action_trail():
    r = Rover(seed=1)
    pl, desk = build_planner(r, "steward", Scripted({"avoid_hazard": ("yes", 0.95)}), 0.7)
    r.start_hazard("rockfall", ahead=110, severity=3)
    run(r, pl, desk, 12)
    pend = events(r, "decision_pending")[0].data
    dec = events(r, "decision")[0].data
    act = events(r, "decision_action")[0].data
    assert pend["ref"] == dec["ref"] == act["ref"]
    assert "boulder field" in pend["prose"] and len(pend["questions"]) == 6
    assert {p["key"] for p in dec["parts"]} >= {"avoid_hazard", "is_severe"}
    assert act["ok"] and "around" in act["summary"]


def test_rules_decisions_also_explain_themselves():
    r = Rover(seed=1)
    pl, desk = build_planner(r, "rules")
    r.start_hazard("dust_storm", severity=3)
    run(r, pl, desk, 3)
    d = events(r, "decision")[0].data
    assert "fixed table" in d["rule"] and events(r, "decision_action")[0].data["ref"] == d["ref"]


def test_the_decision_card_shows_the_question_the_answers_and_the_action():
    from rich.console import Console

    r = Rover(seed=1)
    pl, desk = build_planner(r, "steward", Scripted({"avoid_hazard": ("yes", 0.95)}), 0.7)
    r.start_hazard("rockfall", ahead=110, severity=3)
    panel = DecisionPanel(0.7)
    run(r, pl, desk, 12)
    for e in r.log:  # replay the events into the panel's card builder
        if e.kind == "decision_pending":
            card = new_card(e.data["ref"])
            card.update(
                question=e.data["question"],
                hazard=e.data["hazard"],
                severity=e.data["severity"],
                prose=e.data["prose"],
                questions=e.data["questions"],
            )
            panel.cards[card["ref"]] = card
        elif e.kind == "decision":
            from roverthinking.tui.widgets.decision_panel import _fill_decision

            _fill_decision(panel.cards[e.data["ref"]], e.data)
        elif e.kind == "decision_action":
            panel.cards[e.data["ref"]].update(action=e.data["summary"], action_ok=e.data["ok"])
    text = Console(width=70, record=True, force_terminal=False)
    text.print(format_card(next(iter(panel.cards.values()))))
    out = text.export_text()
    for needle in ("SITUATION", "THE MODEL READ", "ASKED", "Go around it?", "DECIDED", "REROUTE", "ACTION"):
        assert needle in out, needle
