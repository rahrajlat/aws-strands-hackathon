"""Earth Mission Control's brain: one interface, three providers (ollama, bedrock, mock).

Ollama and Bedrock both go through Strands' own model providers; the same `StrandsEarth` class
drives either. Earth is asked for structured answers (validated against `schemas.py`), and any
failure degrades to the deterministic mock rather than crashing the game.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from roverthinking.config import Settings
from roverthinking.earth.schemas import EscalationAnswer, OrderPlan, ScenarioPlan
from roverthinking.sim.hazards import HAZARD_KINDS, POSITIONAL

HAZARD_WORDS = {
    "dust": "dust_storm",
    "storm": "dust_storm",
    "sandstorm": "dust_storm",
    "rockfall": "rockfall",
    "boulder": "rockfall",
    "landslide": "rockfall",
    "rocks": "rockfall",
    "sand": "sand_trap",
    "dune": "sand_trap",
    "trap": "sand_trap",
    "slope": "steep_slope",
    "hill": "steep_slope",
    "cliff": "steep_slope",
    "steep": "steep_slope",
    "sensor": "sensor_fault",
    "camera": "sensor_fault",
    "blind": "sensor_fault",
    "flare": "solar_flare",
    "radiation": "solar_flare",
    "meteor": "meteor_shower",
    "meteors": "meteor_shower",
    "asteroid": "meteor_shower",
    "strange": "strange_rock",
    "interesting": "strange_rock",
    "rock-find": "strange_rock",
    "outcrop": "strange_rock",
}
SEVERITY_WORDS = {
    "minor": 1,
    "small": 1,
    "light": 1,
    "major": 2,
    "heavy": 2,
    "big": 2,
    "catastrophic": 3,
    "massive": 3,
    "severe": 3,
    "huge": 3,
    "extreme": 3,
}


@dataclass(frozen=True)
class EarthReply:
    recommendation: str  # a triage choice or an instrument
    reasoning: str
    message: str = ""
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class EarthModel(Protocol):
    name: str  # provider name: ollama | bedrock | mock
    model_id: str
    interprets_text: bool  # can it understand free-text orders and scenarios?

    def answer_escalation(
        self,
        snapshot: dict[str, Any],
        question: str,
        options: dict[str, str] | list[str],
        local_choice: str,
        local_confidence: float,
    ) -> EarthReply: ...

    def parse_order(self, text: str, snapshot: dict[str, Any]) -> OrderPlan: ...

    def plan_scenario(self, text: str) -> ScenarioPlan: ...


# ---- the offline mock -------------------------------------------------------------------------


class MockEarth:
    """Canned, deterministic replies: always the sensible answer, with a short rationale."""

    name = "mock"
    model_id = "canned"
    interprets_text = False

    def answer_escalation(self, snapshot, question, options, local_choice, local_confidence):
        h = (snapshot.get("hazards") or [{}])[0]
        kind = h.get("kind", "hazard")
        if question == "instrument_choice":
            desc = h.get("description", "")
            pick = "drill" if ("layered" in desc or "clay" in desc) else "spectrometer"
            why = f"The rock looks like {desc or 'ordinary stone'}; the {pick} is the best fit."
            return EarthReply(pick, why, f"Mission Control to Rover: use the {pick}.")
        sev = h.get("severity", 2)
        charges = (snapshot.get("shield") or {}).get("charges", 0)
        if kind in ("rockfall", "sand_trap", "steep_slope"):
            pick = "slow" if sev == 1 and snapshot["wheels_pct"] > 60 else "reroute"
        elif kind == "dust_storm":
            pick = "shield" if sev == 3 and charges else ("stop_and_wait" if sev >= 2 else "slow")
        elif kind == "solar_flare":
            pick = "shield" if sev >= 2 and charges else "safe_mode"
        elif kind == "meteor_shower":
            pick = "shield" if sev >= 2 and charges else ("stop_and_wait" if sev >= 2 else "slow")
        else:
            pick = "safe_mode"
        why = f"A {kind.replace('_', ' ')} of severity {sev} calls for {pick}."
        return EarthReply(pick, why, f"Mission Control to Rover: {pick.replace('_', ' ')}.")

    def parse_order(self, text: str, snapshot: dict[str, Any]) -> OrderPlan:
        return OrderPlan(
            understood=False, action="none", explanation="the mock Earth only understands the strict commands"
        )

    def plan_scenario(self, text: str) -> ScenarioPlan:
        """Keyword matching: good enough to demo free-text scenarios offline."""
        from roverthinking.earth.schemas import ScenarioEventPlan

        words = text.lower().replace(",", " ").replace(".", " ").split()
        sev, events, t = 2, [], 0.0
        for w in words:
            if w in SEVERITY_WORDS:
                sev = SEVERITY_WORDS[w]
            elif w in HAZARD_WORDS:
                kind = HAZARD_WORDS[w]
                if events and events[-1].kind == kind:
                    continue
                events.append(
                    ScenarioEventPlan(
                        kind=kind,
                        severity=sev,
                        after_seconds=t,
                        ahead_m=110.0 if kind in POSITIONAL else None,
                    )
                )
                t += 20.0
        if not events:
            raise ValueError("I could not find any hazard in that text")
        return ScenarioPlan(title="Player scenario", description=text.strip()[:140], events=events[:8])


# ---- Strands-backed providers ---------------------------------------------------------------


ESCALATION_SYSTEM = (
    "You are NASA Mission Control advising a Mars rover that is far away and cannot wait long. "
    "The rover's small onboard model was unsure, so it asked you. Choose exactly one of the offered "
    "options, using the rover state. Notes: the shield has only a few charges, so save it for major or "
    "catastrophic external threats (solar flare, meteor shower, dust storm); terrain hazards such as "
    "boulders, sand traps and steep slopes are handled by going around (reroute) or crawling (slow); "
    "a fault in the rover's own sensors needs safe mode. Be concise."
)
ORDER_SYSTEM = (
    "You translate a Mars Mission Control operator's free-text order into exactly one supported "
    "rover command: stop; drive (normal or slow); safe_mode; shield (raise it); unshield (lower it); "
    "back_up (reverse out when stuck); sample (with camera, spectrometer, drill or skip). If the text "
    "is not an order the rover can run, set understood=false."
)
SCENARIO_SYSTEM = (
    "You turn a game player's free-text scenario into a schedule of hazards for a Mars rover game. "
    "You may ONLY use these hazard kinds: " + ", ".join(HAZARD_KINDS) + ". Severity is 1 (minor), "
    "2 (major) or 3 (catastrophic). Terrain hazards (rockfall, sand_trap, steep_slope) and "
    "strange_rock need ahead_m between 60 and 250; the others affect the rover directly and need no "
    "distance. Space events 15-40 seconds apart, at most 8, and keep it playable."
)


class StrandsEarth:
    """Earth driven by a Strands model provider (Ollama or Amazon Bedrock)."""

    interprets_text = True

    def __init__(self, model: Any, name: str, model_id: str) -> None:
        self.model, self.name, self.model_id = model, name, model_id
        self._fallback = MockEarth()

    def _ask(self, system: str, prompt: str, schema: type) -> tuple[Any, float, int, int]:
        from strands import Agent

        agent = Agent(model=self.model, system_prompt=system, callback_handler=None)
        t0 = time.perf_counter()
        result = agent(prompt, structured_output_model=schema)
        ms = (time.perf_counter() - t0) * 1000
        usage = getattr(result.metrics, "accumulated_usage", {}) or {}
        out = result.structured_output
        if out is None:
            raise ValueError("the model returned no structured answer")
        return out, ms, int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))

    def answer_escalation(self, snapshot, question, options, local_choice, local_confidence):
        opts = options if isinstance(options, dict) else {o: "" for o in options}
        lines = "\n".join(f"- {k}: {v}" if v else f"- {k}" for k, v in opts.items())
        prompt = (
            f"Rover state: {snapshot}\n\nThe rover asked: {question.replace('_', ' ')}.\n"
            f"Its own best guess was '{local_choice}' with confidence {local_confidence:.2f}.\n"
            f"Options:\n{lines}\n\nChoose one option name for 'recommendation'."
        )
        try:
            ans, ms, tin, tout = self._ask(ESCALATION_SYSTEM, prompt, EscalationAnswer)
            rec = ans.recommendation.strip().lower().replace(" ", "_")
            if rec not in opts:
                rec = next((o for o in opts if o in rec or rec in o), "")
            if not rec:
                raise ValueError(f"Earth recommended {ans.recommendation!r}, which is not an option")
            return EarthReply(rec, ans.reasoning, ans.message, ms, tin, tout)
        except Exception as exc:  # noqa: BLE001 - never let Earth crash the game
            fb = self._fallback.answer_escalation(snapshot, question, opts, local_choice, local_confidence)
            return EarthReply(
                fb.recommendation, f"{fb.reasoning} (offline answer: {str(exc)[:60]})", fb.message, 0.0
            )

    def parse_order(self, text, snapshot):
        prompt = f"Rover state: {snapshot}\n\nOperator order: {text!r}"
        out, *_ = self._ask(ORDER_SYSTEM, prompt, OrderPlan)
        return out

    def plan_scenario(self, text):
        out, *_ = self._ask(SCENARIO_SYSTEM, f"Player scenario: {text}", ScenarioPlan)
        return out


# ---- startup: build the configured provider, or say why we fell back ----------------------------


def _ollama_model(s: Settings):
    from strands.models.ollama import OllamaModel

    args: dict[str, Any] = {}
    if s.ollama_api_key:
        args["headers"] = {"Authorization": f"Bearer {s.ollama_api_key}"}
    extra: dict[str, Any] = {}
    mid = s.earth_model.lower()
    if mid.startswith("gpt-oss"):
        extra["think"] = "low"  # keep reasoning short so replies come back quickly
    return OllamaModel(
        s.ollama_host,
        ollama_client_args=args or None,
        model_id=s.earth_model,
        temperature=0.2,
        additional_args=extra or None,
    )


def is_ollama_cloud(s: Settings) -> bool:
    """Roverthinking only uses Ollama Cloud models: `*-cloud` via the signed-in local Ollama app,
    or any model served directly by ollama.com with an API key."""
    return s.earth_model.endswith("-cloud") or "ollama.com" in s.ollama_host


def check_ollama(s: Settings) -> str | None:
    """Return None if Ollama Cloud is usable with this model, else a reason."""
    direct = "ollama.com" in s.ollama_host
    if not is_ollama_cloud(s):
        return (
            f"'{s.earth_model}' is a local model; Roverthinking uses Ollama Cloud models such as "
            "gpt-oss:120b-cloud (run `ollama signin`), or set OLLAMA_HOST=https://ollama.com with "
            "OLLAMA_API_KEY"
        )
    if direct and not s.ollama_api_key:
        return "OLLAMA_HOST is ollama.com but OLLAMA_API_KEY is not set"
    req = urllib.request.Request(s.ollama_host.rstrip("/") + "/api/tags")
    if s.ollama_api_key:
        req.add_header("Authorization", f"Bearer {s.ollama_api_key}")
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            import json

            names = {m["name"] for m in json.loads(r.read()).get("models", [])}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        where = "Ollama Cloud" if direct else f"Ollama at {s.ollama_host}"
        hint = "check OLLAMA_API_KEY" if direct else "start it with `ollama serve`"
        return f"{where} is not reachable ({exc}); {hint}"
    # Cloud models (`*-cloud` via the signed-in local app) are resolved on demand and do NOT
    # appear in /api/tags unless explicitly pulled, so membership there is not a usability test.
    # The successful /api/tags call above already confirms the daemon is up; a non-cloud local
    # model, however, must actually be present.
    if not direct and not s.earth_model.endswith("-cloud") and s.earth_model not in names:
        return (
            f"the Ollama app has no model {s.earth_model!r}: run "
            f"`ollama pull {s.earth_model}`"
        )
    return None


def check_bedrock(s: Settings) -> str | None:
    try:
        import boto3

        session = boto3.Session(region_name=s.aws_region)
        if session.get_credentials() is None:
            return (
                "no AWS credentials found (set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, run "
                "`aws configure`, or use SSO)"
            )
    except Exception as exc:  # noqa: BLE001
        return f"could not initialise AWS: {exc}"
    if not s.earth_model:
        return "ROVER_EARTH_MODEL is not set; give it a Bedrock inference-profile ID such as global.anthropic.claude-haiku-5-5"
    return None


def make_earth(s: Settings) -> tuple[EarthModel, str]:
    """Build the configured Earth. On any problem return the mock and say why."""
    if s.earth_provider == "mock":
        return MockEarth(), "Earth: mock (canned replies)"
    problem = check_ollama(s) if s.earth_provider == "ollama" else check_bedrock(s)
    if problem:
        return MockEarth(), f"Earth provider '{s.earth_provider}' unavailable, using mock: {problem}"
    try:
        if s.earth_provider == "ollama":
            return StrandsEarth(_ollama_model(s), "ollama", s.earth_model), (
                f"Earth: Ollama {s.earth_model} at {s.ollama_host}"
            )
        from strands.models import BedrockModel

        model = BedrockModel(model_id=s.earth_model, region_name=s.aws_region, temperature=0.2)
        return StrandsEarth(model, "bedrock", s.earth_model), (
            f"Earth: Amazon Bedrock {s.earth_model} in {s.aws_region}"
        )
    except Exception as exc:  # noqa: BLE001
        return MockEarth(), f"Earth provider '{s.earth_provider}' failed to start, using mock: {exc}"
