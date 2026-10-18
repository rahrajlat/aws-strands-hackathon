"""Typed questions loaded from YAML decision configs via decider_framework.

All questions are sourced from `configs/*.yaml`. The Python constants here are
derived from those YAML files so callers that import them directly continue to work.
"""

from __future__ import annotations

from pathlib import Path

from roverthinking.decisions.model import Question, yes_no

from decider_framework.schema import DecisionConfig

_CONFIGS = Path(__file__).parent / "configs"


def _load(name: str) -> DecisionConfig:
    return DecisionConfig.from_yaml(_CONFIGS / f"{name}.yaml")


def _q(cfg: DecisionConfig, key: str) -> Question:
    for spec in cfg.all_questions():
        if spec.key == key:
            if spec.type == "yesno":
                return yes_no(spec.key, spec.text, spec.yes, spec.no, short=spec.short)
            return Question(spec.key, spec.text, spec.options, kind="choice", short=spec.short)
    raise KeyError(f"question {key!r} not found in {cfg.name!r}")


_triage_cfg = _load("hazard_triage")
_instr_cfg = _load("instrument_choice")
_stale_cfg = _load("stale_order_check")

# Split classifiers for hazard triage
IS_EXTERNAL_THREAT = _q(_triage_cfg, "is_external_threat")
IS_SEVERE = _q(_triage_cfg, "is_severe")
NEEDS_SAFE_MODE = _q(_triage_cfg, "needs_safe_mode")
AVOID_HAZARD = _q(_triage_cfg, "avoid_hazard")
WAIT_IT_OUT = _q(_triage_cfg, "wait_it_out")
CAN_WAIT = _q(_triage_cfg, "can_wait")

# Instrument choice classifier
DRILL_WORTHWHILE = _q(_instr_cfg, "drill_worthwhile")

# Stale order check
ORDER_STILL_VALID = _q(_stale_cfg, "order_valid")

# Synthetic "all actions" questions for display/logging — not asked to Decider
HAZARD_TRIAGE = Question(
    key="hazard_triage",
    short="How to respond?",
    text="Given this hazard and the rover's state, how should the rover respond?",
    options={
        "proceed": "carry on at normal speed; the hazard is harmless or already handled",
        "slow": "crawl through carefully; the hazard is passable with care",
        "reroute": "detour around the hazard; it would damage or trap the rover",
        "stop_and_wait": "stop here and wait; driving now is unsafe but the hazard will pass",
        "safe_mode": "reboot in safe mode; the rover's own sensors or electronics are faulty",
        "shield": "raise the protective shield; a severe threat from outside would damage the rover",
        "back_up": "reverse out; the rover is stuck and must free itself",
    },
)

INSTRUMENT_CHOICE = Question(
    key="instrument_choice",
    short="Which instrument?",
    text="Which instrument should examine this rock?",
    options={
        "camera": "photograph only; cheap and safe, little science",
        "spectrometer": "laser spectrometer; good for dry rock, ice and metal, safe on anything",
        "drill": "drill a core; best for layered sedimentary or clay rock",
        "skip": "not worth the time",
    },
)
