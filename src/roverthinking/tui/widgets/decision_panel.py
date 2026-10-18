"""The decision card: what the rover was shown, what it was asked, what it answered, and what it did.

The card is built from the simulation's `decision_pending`, `decision` and `decision_action`
events, so it appears the moment a model is asked (with a "thinking" state), fills in as the
answers arrive, and ends with the action the rover actually took.
"""

from __future__ import annotations

import time
from typing import Any

from rich.panel import Panel
from rich.text import Text
from textual.widgets import Static

from roverthinking.sim.rover import SimEvent

QUESTION_LABEL = {
    "hazard_triage": "How should the rover respond?",
    "instrument_choice": "Which instrument should examine the rock?",
    "order_valid": "Is this order from Earth still valid and safe?",
    "reply_check": "Is Earth's advice still valid and safe?",
}
HAZARD_LABEL = {
    "dust_storm": "dust storm",
    "rockfall": "boulder field",
    "sand_trap": "sand trap",
    "steep_slope": "steep slope",
    "sensor_fault": "sensor fault",
    "solar_flare": "solar flare",
    "meteor_shower": "meteor shower",
    "strange_rock": "strange rock",
    "order": "order from Earth",
}
WHO = {
    "strands-decider-2b": "Decider 2B",
    "mock-decider": "mock decider",
    "reflex": "reflex",
    "earth": "Earth",
    "fallback-rules": "rules table (decider offline)",
}


def bar(frac: float, width: int = 8) -> str:
    n = max(0, min(width, round(frac * width)))
    return "█" * n + "░" * (width - n)


def conf_color(c: float) -> str:
    return "green" if c >= 0.7 else ("yellow" if c >= 0.5 else "red")


def new_card(ref: int) -> dict[str, Any]:
    return {"ref": ref, "status": "asking", "parts": [], "questions": [], "t0": time.monotonic()}


def card_from_record(rec: Any) -> dict[str, Any]:
    """Build a card from a stored DecisionRecord (used by the inspector)."""
    d = rec.data
    card = new_card(d.get("ref") or rec.index + 1)
    card.update(
        status="done",
        question=d["question"],
        hazard=d["hazard"],
        severity=d["severity"],
        prose=d.get("prose", ""),
        parts=list(d.get("parts") or []),
        questions=[{"key": p["key"], "short": p["short"]} for p in d.get("parts") or []],
    )
    _fill_decision(card, d)
    card["action"] = rec.action
    card["action_ok"] = rec.action_ok
    return card


def _fill_decision(card: dict[str, Any], d: dict[str, Any]) -> None:
    if d.get("source") == "earth" and card.get("parts"):
        # Earth's advice follows the validity check on the same card: keep that check's wording
        card.update(
            choice=d["choice"],
            confidence=d["confidence"],
            source="earth",
            reasoning=d.get("reasoning", ""),
            latency=0,
            status="decided",
        )
        return
    card.update(
        question=d["question"],
        hazard=d["hazard"],
        severity=d["severity"],
        choice=d["choice"],
        confidence=d["confidence"],
        source=d.get("source") or d["planner"],
        latency=d.get("latency_ms", 0),
        escalated=bool(d.get("escalated")),
        uncertain=bool(d.get("under_uncertainty")),
        rule=d.get("rule", ""),
        reasoning=d.get("reasoning", ""),
        late=bool(d.get("late")),
    )
    if d.get("parts"):
        card["parts"] = list(d["parts"])
    if d.get("prose"):
        card["prose"] = d["prose"]
    card["status"] = "decided"


def format_card(card: dict[str, Any], threshold: float = 0.7, t: float = 0.0) -> Panel:
    status = card.get("status", "asking")
    key = card.get("question", "")
    hazard = HAZARD_LABEL.get(card.get("hazard", ""), card.get("hazard", ""))
    sev = {1: "minor", 2: "major", 3: "catastrophic"}.get(card.get("severity", 0), "")
    border = "yellow" if status == "asking" else "green"
    if card.get("escalated"):
        border = "magenta"
    if card.get("action") and not card.get("action_ok", True):
        border = "red"

    out = Text()
    out.append("SITUATION  ", style="bold cyan")
    out.append(f"{sev + ' ' if sev else ''}{hazard}\n", style="bold")
    prose = card.get("prose")
    if prose:
        out.append("THE MODEL READ\n", style="bold cyan")
        out.append(f"“{prose}”\n", style="italic dim")
    q = QUESTION_LABEL.get(key, key)
    if card.get("questions"):
        out.append("ASKED  ", style="bold cyan")
        out.append(f"{q}\n" if q else "\n")
        answers = {p["key"]: p for p in card.get("parts", [])}
        for item in card["questions"]:
            p = answers.get(item["key"])
            out.append(f"  ▸ {item['short']:<27}", style="white")
            if p is None:
                out.append("…\n", style="yellow")
            else:
                c = p["confidence"]
                out.append(
                    f"{p['answer'].upper():<4}", style="bold " + ("green" if p["answer"] == "yes" else "red")
                )
                out.append(f" {bar(c)} ", style=conf_color(c))
                out.append(f"{c:.2f}\n", style=conf_color(c))
    elif card.get("rule"):
        out.append("HOW  ", style="bold cyan")
        out.append(f"{card['rule']}\n")

    if status == "asking":
        dots = "." * (1 + int(t * 3) % 3)
        out.append(f"\n⏳ thinking{dots}\n", style="bold yellow")
    elif "choice" in card:
        c = card["confidence"]
        out.append("\nDECIDED  ", style="bold cyan")
        out.append(f"{card['choice'].replace('_', ' ').upper()}", style="bold white on #2a5d34")
        if card.get("source") != "reflex" and (
            card.get("parts") or card.get("question") in ("hazard_triage", "instrument_choice")
        ):
            out.append(f"  {bar(c)} {c:.2f}", style=conf_color(c))
        out.append("\n")
        who = WHO.get(card.get("source", ""), card.get("source", ""))
        lat = f" · {card['latency']:.0f} ms" if card.get("latency") else ""
        out.append(f"  by {who}{lat}", style="dim")
        if card.get("parts") and card.get("question") in ("hazard_triage", "instrument_choice"):
            out.append(f"   (acts at ≥ {threshold:.2f})", style="dim")
        out.append("\n")
        if card.get("escalated"):
            out.append("  ⚠ below the threshold and safe to wait: asked Earth\n", style="magenta")
        if card.get("uncertain"):
            out.append("  ⚠ low confidence but cannot wait: acted on its best guess\n", style="yellow")
        if card.get("reasoning"):
            out.append(f"  Earth says: {card['reasoning']}\n", style="magenta")
    if card.get("action"):
        text = card["action"]
        ok = card.get("action_ok", True) and not text.startswith("✗")
        out.append("ACTION   ", style="bold cyan")
        out.append(("✓ " + text) if ok else text, style="bold green" if ok else "bold red")
        out.append("\n")
    elif status == "decided":
        out.append("ACTION   …\n", style="yellow")
    title = f"DECISION #{card.get('ref', '?')}"
    return Panel(out, title=title, title_align="left", border_style=border, padding=(0, 1))


class DecisionPanel(Static):
    def __init__(self, threshold: float = 0.7, **kw) -> None:
        super().__init__(**kw)
        self.threshold = threshold
        self.cards: dict[int, dict[str, Any]] = {}
        self.latest: int | None = None
        self.count = 0

    def on_mount(self) -> None:
        self.update(
            Panel(
                Text(
                    "Waiting for something to decide...\nThrow a hazard at the rover (d r t h m n f g).",
                    style="dim",
                ),
                title="DECISION",
                title_align="left",
                border_style="grey50",
            )
        )

    def ingest(self, events: list[SimEvent]) -> None:
        changed = False
        for e in events:
            d, k = e.data, e.kind
            if k == "decision_pending":
                card = new_card(d["ref"])
                card.update(
                    question=d["question"],
                    hazard=d["hazard"],
                    severity=d["severity"],
                    prose=d["prose"],
                    questions=d["questions"],
                )
                self.cards[d["ref"]] = card
                self.latest = d["ref"]
                changed = True
            elif k == "decision" and d.get("ref") is not None:
                card = self.cards.get(d["ref"])
                if card is None:
                    card = new_card(d["ref"])
                    self.cards[d["ref"]] = card
                _fill_decision(card, d)
                self.latest = d["ref"]
                changed = True
            elif k == "decision_action":
                card = self.cards.get(d["ref"]) if d.get("ref") is not None else None
                if card is not None:
                    card["action"], card["action_ok"] = d["summary"], d["ok"]
                    card["status"] = "done"
                    changed = True
        if changed:
            self.count = len(self.cards)
            self.redraw(0.0)

    def redraw(self, t: float) -> None:
        if self.latest is None:
            return
        card = self.cards[self.latest]
        panel = format_card(card, self.threshold, t)
        panel.title = f"DECISION #{card['ref']}  ·  {self.count} so far (i to browse)"
        self.update(panel)
