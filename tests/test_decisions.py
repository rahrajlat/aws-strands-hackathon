import pytest

from roverthinking.config import load_settings
from roverthinking.decisions.model import Decider2B, MockDecider, Question
from roverthinking.decisions.questions import HAZARD_TRIAGE, ORDER_STILL_VALID

STATE = {"wheels_pct": 42, "hazards": [{"kind": "rockfall", "severity": 2}]}


def test_mock_is_deterministic_and_valid():
    m = MockDecider()
    a, b = m.decide(STATE, HAZARD_TRIAGE), m.decide(STATE, HAZARD_TRIAGE)
    assert a == b
    assert a.choice in HAZARD_TRIAGE.options
    assert 0.5 <= a.confidence <= 1.0
    assert sum(a.probabilities.values()) == pytest.approx(1.0)


def test_mock_rules_override():
    m = MockDecider(rules=lambda s, q: ("reroute", 0.9))
    assert m.decide(STATE, HAZARD_TRIAGE).choice == "reroute"


def test_question_validation():
    with pytest.raises(ValueError):
        Question("q", "?", {"only": "one"})


def test_payload_shapes():
    p = Decider2B().build_payload(STATE, HAZARD_TRIAGE)["questions"]["hazard_triage"]
    assert p["type"] == "choice" and set(p["criteria"]) == set(HAZARD_TRIAGE.options)
    p = Decider2B().build_payload(STATE, ORDER_STILL_VALID)["questions"]["order_valid"]
    assert p["type"] == "noul" and set(p["criteria"]) == {"true", "false"}


def test_parse_server_answers():
    d = Decider2B.parse_answer(
        HAZARD_TRIAGE,
        {
            "type": "choice",
            "choice": "reroute",
            "confidence": 0.8,
            "probabilities": {"reroute": 0.9, "slow": 0.1},
        },
        120.0,
    )
    assert (d.choice, d.confidence, d.latency_ms) == ("reroute", 0.8, 120.0)
    d = Decider2B.parse_answer(ORDER_STILL_VALID, {"type": "noul", "noul": 0.2}, 90.0)
    assert d.choice == "no" and d.confidence == pytest.approx(0.8)


def test_settings_override_and_validation(monkeypatch):
    monkeypatch.setenv("ROVER_EARTH_PROVIDER", "ollama")
    assert load_settings(earth="mock").earth_provider == "mock"
    with pytest.raises(ValueError):
        load_settings(earth="gpt")
