"""The DecisionModel interface, a deterministic mock, and the Strands Decider 2B client.

Decider 2B is served locally (`strands-decider serve <checkpoint> --port 8099`) and spoken
to over `POST /v1/systemone`. We use `urllib` so the game needs no torch to run.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Question:
    """A typed question. `options` maps option name -> what it means.

    kind "choice": pick one option. kind "yesno": options are fixed to yes/no.
    """

    key: str
    text: str
    options: dict[str, str]
    kind: str = "choice"
    short: str = ""  # a few words for on-screen display, e.g. "Go around it?"

    def __post_init__(self) -> None:
        if self.kind not in ("choice", "yesno"):
            raise ValueError(f"unknown question kind {self.kind!r}")
        if self.kind == "choice" and len(self.options) < 2:
            raise ValueError("a choice question needs at least two options")


def yes_no(key: str, text: str, yes: str, no: str, short: str = "") -> Question:
    return Question(key, text, {"yes": yes, "no": no}, kind="yesno", short=short)


@dataclass(frozen=True)
class Decision:
    """The model's answer. `confidence` is 0..1; for yes/no it is the certainty of the pick."""

    question: str
    choice: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)
    latency_ms: float = 0.0
    source: str = "mock"
    detail: tuple[dict[str, Any], ...] = ()  # the narrow questions behind a combined decision


class DecisionModel(Protocol):
    name: str

    def decide(self, state: dict[str, Any], question: Question) -> Decision: ...

    def decide_many(self, state: dict[str, Any], questions: list[Question]) -> list[Decision]: ...


class DeciderUnavailable(RuntimeError):
    """The Decider server is not reachable."""


class MockDecider:
    """Deterministic stand-in: the same state + question always gives the same answer.

    An optional `rules` callable (state, question) -> (choice, confidence) lets the
    simulation supply sensible answers; otherwise a hash picks the option.
    """

    name = "mock-decider"

    def __init__(self, rules=None, latency_ms: float = 115.0) -> None:
        self._rules = rules
        self._latency = latency_ms

    def decide(self, state: dict[str, Any], question: Question) -> Decision:
        if self._rules is not None:
            result = self._rules(state, question)
            if result is not None:
                choice, conf = result
                return self._make(question, choice, conf)
        digest = hashlib.sha256(
            (json.dumps(state, sort_keys=True, default=str) + question.key).encode()
        ).digest()
        names = list(question.options)
        choice = names[digest[0] % len(names)]
        conf = 0.5 + (digest[1] / 255) * 0.5
        return self._make(question, choice, conf)

    def decide_many(self, state: dict[str, Any], questions: list[Question]) -> list[Decision]:
        return [self.decide(state, q) for q in questions]

    def _make(self, question: Question, choice: str, conf: float) -> Decision:
        if choice not in question.options:
            raise ValueError(f"{choice!r} is not an option of {question.key}")
        rest = (1 - conf) / max(len(question.options) - 1, 1)
        probs = {o: (conf if o == choice else rest) for o in question.options}
        return Decision(question.key, choice, conf, probs, self._latency, self.name)


class Decider2B:
    """Client for a locally served Strands Decider 2B."""

    name = "strands-decider-2b"

    def __init__(self, url: str = "http://127.0.0.1:8099", timeout: float = 120.0, render=None) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.render = render or (lambda state: json.dumps(state, sort_keys=True, default=str))
        # The server's behaviour under concurrent requests is unverified: serialise.
        self._lock = threading.Lock()

    def health(self) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=5) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeciderUnavailable(
                f"no Decider server at {self.url} ({exc}). "
                "Start one with: strands-decider serve <checkpoint> --port 8099"
            ) from exc

    @staticmethod
    def _question_payload(question: Question) -> dict[str, Any]:
        if question.kind == "yesno":
            return {
                "type": "noul",
                "instructions": question.text,
                "criteria": {"true": question.options["yes"], "false": question.options["no"]},
            }
        return {"type": "choice", "instructions": question.text, "criteria": question.options}

    def build_payload(self, state: dict[str, Any], question: Question | list[Question]) -> dict[str, Any]:
        qs = question if isinstance(question, list) else [question]
        return {
            "state": self.render(state),
            "model": "roverthinking",
            "questions": {q.key: self._question_payload(q) for q in qs},
        }

    @staticmethod
    def parse_answer(question: Question, answer: dict[str, Any], latency_ms: float) -> Decision:
        if question.kind == "yesno":
            p = float(answer["noul"])
            choice = "yes" if p >= 0.5 else "no"
            return Decision(
                question.key,
                choice,
                max(p, 1 - p),
                {"yes": p, "no": 1 - p},
                latency_ms,
                Decider2B.name,
            )
        return Decision(
            question.key,
            answer["choice"],
            float(answer["confidence"]),
            {k: float(v) for k, v in answer["probabilities"].items()},
            latency_ms,
            Decider2B.name,
        )

    def decide(self, state: dict[str, Any], question: Question) -> Decision:
        return self.decide_many(state, [question])[0]

    def decide_many(self, state: dict[str, Any], questions: list[Question]) -> list[Decision]:
        """Several questions about one state in a single request (the state is encoded once).

        A transient HTTP failure is retried once before it is reported.
        """
        try:
            return self._decide_many(state, questions)
        except DeciderUnavailable as exc:
            if "HTTP" not in str(exc):
                raise
            return self._decide_many(state, questions)

    def _decide_many(self, state: dict[str, Any], questions: list[Question]) -> list[Decision]:
        body = json.dumps(self.build_payload(state, questions)).encode()
        req = urllib.request.Request(
            f"{self.url}/v1/systemone", data=body, headers={"content-type": "application/json"}
        )
        with self._lock:
            start = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read())
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:300].decode("utf-8", "replace")
                raise DeciderUnavailable(f"HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise DeciderUnavailable(str(exc)) from exc
            wall_ms = (time.perf_counter() - start) * 1000
        latency = float(payload.get("latency_ms") or wall_ms)
        return [self.parse_answer(q, payload["answers"][q.key], latency) for q in questions]


ESCAPE_Q = yes_no("warmup", "Is this a test?", yes="a test", no="not a test")


def _mock() -> MockDecider:
    from roverthinking.decisions.mock_rules import mock_rules

    return MockDecider(rules=mock_rules)


def make_decider(mode: str, url: str) -> tuple[DecisionModel, str]:
    """Pick a decider. Returns (model, note). 'auto' falls back to the mock if no server."""
    if mode == "mock":
        return _mock(), "mock decider (forced)"
    from roverthinking.decisions.render import as_prose
    from roverthinking.sim.rover import Rover

    client = Decider2B(url, render=as_prose)
    try:
        info = client.health()
    except DeciderUnavailable as exc:
        if mode == "server":
            raise
        return _mock(), f"Decider server not reachable, using mock ({exc})"
    try:  # the first request compiles GPU kernels (~5 s); do it now, not mid-game
        client.decide(Rover().snapshot(), ESCAPE_Q)
    except Exception:  # noqa: BLE001 - warm-up is best effort
        pass
    return client, f"Decider 2B at {url} ({info.get('checkpoint', '?')})"
