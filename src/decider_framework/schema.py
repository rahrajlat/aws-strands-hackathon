"""YAML schema for Decider 2B decision configs — loaded with PyYAML, validated here.

YAML layout
-----------
name: hazard-triage
description: "..."
pattern: split-and-combine

questions:
  - key: is_external_threat
    type: yesno          # or "choice"
    text: "Is the hazard weather or space?"
    short: "Weather or space?"
    yes: "a solar flare, meteor shower or dust storm"
    no: "terrain or rover fault"

escalation:
  key: can_wait
  type: yesno
  text: "Can the rover safely wait for Earth's reply?"
  yes: "waiting is safe"
  no: "waiting is dangerous"

policy:
  threshold: 0.70
  rules:
    - when: {is_external_threat: yes, is_severe: yes}
      then: shield
      require_context: shield_ready   # skipped when context[key] is falsy
      name: "shield severe external threat"

    - when: {needs_safe_mode: yes}
      then: safe_mode

  default: slow
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class QuestionSpec:
    key: str
    type: str  # "yesno" | "choice"
    text: str
    short: str = ""
    yes: str = ""
    no: str = ""
    options: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in ("yesno", "choice"):
            raise ValueError(f"question {self.key!r}: type must be 'yesno' or 'choice', got {self.type!r}")
        if self.type == "yesno" and (not self.yes or not self.no):
            raise ValueError(f"question {self.key!r}: yesno questions need 'yes' and 'no' descriptions")
        if self.type == "choice" and len(self.options) < 2:
            raise ValueError(f"question {self.key!r}: choice questions need at least two options")


@dataclass(frozen=True)
class RuleSpec:
    then: str
    when: dict[str, str] = field(default_factory=dict)
    require_context: str = ""
    name: str = ""


@dataclass(frozen=True)
class PolicySpec:
    threshold: float = 0.70
    rules: tuple[RuleSpec, ...] = ()
    default: str = "fallback"


@dataclass(frozen=True)
class DecisionConfig:
    name: str
    description: str
    pattern: str
    questions: tuple[QuestionSpec, ...]
    policy: PolicySpec
    escalation: QuestionSpec | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DecisionConfig":
        raw: dict[str, Any] = yaml.safe_load(Path(path).read_text())
        return cls._parse(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DecisionConfig":
        return cls._parse(raw)

    @classmethod
    def _parse(cls, raw: dict[str, Any]) -> "DecisionConfig":
        questions = tuple(_parse_question(q) for q in raw.get("questions", []))
        escalation = _parse_question(raw["escalation"]) if "escalation" in raw else None
        pol = raw.get("policy", {})
        rules = tuple(_parse_rule(r) for r in pol.get("rules", []))
        policy = PolicySpec(
            threshold=float(pol.get("threshold", 0.70)),
            rules=rules,
            default=str(pol.get("default", "fallback")),
        )
        return cls(
            name=str(raw["name"]),
            description=str(raw.get("description", "")),
            pattern=str(raw.get("pattern", "policy")),
            questions=questions,
            policy=policy,
            escalation=escalation,
        )

    def all_questions(self) -> tuple[QuestionSpec, ...]:
        """All questions including the escalation question (if any)."""
        if self.escalation is None:
            return self.questions
        return self.questions + (self.escalation,)


def _parse_question(raw: dict[str, Any]) -> QuestionSpec:
    # PyYAML parses bare `yes:` and `no:` keys as boolean True/False.
    # We check both string and boolean forms so either quoting style works.
    def _get(key_str: str, key_bool: bool) -> str:
        for k in (key_str, key_bool):
            if k in raw:
                return str(raw[k])
        return ""

    opts: dict[str, str] = {}
    if raw.get("type") != "yesno":
        opts = {str(k): str(v) for k, v in raw.get("options", {}).items()}
    return QuestionSpec(
        key=str(raw["key"]),
        type=str(raw.get("type", "yesno")),
        text=str(raw["text"]),
        short=str(raw.get("short", "")),
        yes=_get("yes", True),
        no=_get("no", False),
        options=opts,
    )


def _parse_rule(raw: dict[str, Any]) -> RuleSpec:
    # PyYAML parses bare `yes`/`no` as booleans in flow mappings too.
    def _val(v: Any) -> str:
        if v is True:
            return "yes"
        if v is False:
            return "no"
        return str(v)

    when = {str(k): _val(v) for k, v in raw.get("when", {}).items()}
    return RuleSpec(
        then=str(raw["then"]),
        when=when,
        require_context=str(raw.get("require_context", "")),
        name=str(raw.get("name", "")),
    )
