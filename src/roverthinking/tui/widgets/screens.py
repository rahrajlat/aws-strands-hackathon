"""Modal screens: the scenario wizard, the decision inspector and the end report."""

from __future__ import annotations

import json

from rich.panel import Panel
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from roverthinking.decisions import questions as Q
from roverthinking.game.scenarios import (
    AHEAD_CHOICES,
    HAZARD_LABELS,
    SEVERITY_NAMES,
    ScenarioEvent,
)
from roverthinking.game.session import DecisionRecord, GameSession
from roverthinking.sim.hazards import POSITIONAL
from roverthinking.sim.scoring import summarize
from roverthinking.tui.widgets.decision_panel import card_from_record, format_card

WHEN_CHOICES = {
    "now": 0.0,
    "in 10 seconds": 10.0,
    "in 30 seconds": 30.0,
    "chained: 20 s after the last one": 20.0,
}


class WizardScreen(ModalScreen):
    """what -> where -> how bad -> when -> preview card."""

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("backspace", "back", "Back")]
    DEFAULT_CSS = """
    WizardScreen { align: center middle; }
    #wizard { width: 64; height: auto; max-height: 28; border: round $accent; padding: 1 2;
              background: $surface; }
    #wiz-title { text-style: bold; margin-bottom: 1; }
    #wiz-card { margin-top: 1; }
    """

    def __init__(self, session: GameSession) -> None:
        super().__init__()
        self.session = session
        self.kind = ""
        self.ahead: float | None = None
        self.sev = 2
        self.delay = 0.0
        self.step = "what"
        self.history: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard"):
            yield Static("", id="wiz-title")
            yield OptionList(id="wiz-options")
            yield Static("", id="wiz-card")

    def on_mount(self) -> None:
        self._show("what")

    def _show(self, step: str) -> None:
        self.step = step
        ol = self.query_one("#wiz-options", OptionList)
        ol.clear_options()
        card = self.query_one("#wiz-card", Static)
        title = self.query_one("#wiz-title", Static)
        card.update("")
        if step == "what":
            title.update("1/4  What should happen to the rover?")
            ol.add_options([Option(label, id=k) for k, label in HAZARD_LABELS.items()])
        elif step == "where":
            title.update("2/4  How far ahead?")
            ol.add_options([Option(k, id=k) for k in AHEAD_CHOICES])
        elif step == "how":
            title.update("3/4  How bad?")
            ol.add_options([Option(n.title(), id=str(i)) for i, n in SEVERITY_NAMES.items()])
        elif step == "when":
            title.update("4/4  When?")
            ol.add_options([Option(k, id=k) for k in WHEN_CHOICES])
        elif step == "preview":
            title.update("Preview: Enter to launch, Backspace to go back, Esc to cancel")
            ev = self._event()
            when = next(k for k, v in WHEN_CHOICES.items() if v == self.delay)
            card.update(Panel(Text(f"{ev.describe()}\nwhen: {when}"), title="scenario"))
            ol.add_options([Option("Launch", id="go")])
        ol.focus()
        if ol.option_count:
            ol.highlighted = 0

    def _event(self) -> ScenarioEvent:
        return ScenarioEvent(0, self.kind, self.ahead, self.sev)

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        oid = ev.option.id or ""
        self.history.append(self.step)
        if self.step == "what":
            self.kind = oid
            self.ahead = None
            self._show("where" if oid in POSITIONAL else "how")
        elif self.step == "where":
            self.ahead = AHEAD_CHOICES[oid]
            self._show("how")
        elif self.step == "how":
            self.sev = int(oid)
            self._show("when")
        elif self.step == "when":
            self.delay = WHEN_CHOICES[oid]
            self._show("preview")
        else:
            self.dismiss((self._event(), self.delay))

    def action_back(self) -> None:
        if self.history:
            self._show(self.history.pop())

    def action_cancel(self) -> None:
        self.dismiss(None)


class InspectorScreen(ModalScreen):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("i", "close", "Close")]
    DEFAULT_CSS = """
    InspectorScreen { align: center middle; }
    #inspector { width: 130; height: 40; border: round $accent; background: $surface; }
    #insp-list { width: 38; border-right: solid $primary; }
    #insp-detail { width: 1fr; padding: 0 1; overflow-y: auto; }
    """

    def __init__(self, session: GameSession, earth_label: str) -> None:
        super().__init__()
        self.session = session
        self.earth_label = earth_label

    def compose(self) -> ComposeResult:
        with Horizontal(id="inspector"):
            yield OptionList(id="insp-list")
            yield Static("", id="insp-detail")

    def on_mount(self) -> None:
        ol = self.query_one("#insp-list", OptionList)
        recs = self.session.decisions
        if not recs:
            self.query_one("#insp-detail", Static).update(
                "No decisions yet. Throw a hazard at the rover (d, r, t, ...)."
            )
            return
        for rec in reversed(recs):
            d = rec.data
            ol.add_option(
                Option(
                    f"#{rec.index + 1} {d['choice']} {d['confidence']:.2f}  {d['hazard']}",
                    id=str(rec.index),
                )
            )
        ol.focus()
        ol.highlighted = 0

    def on_option_list_option_highlighted(self, ev: OptionList.OptionHighlighted) -> None:
        if ev.option.id is None:
            return
        rec = self.session.decisions[int(ev.option.id)]
        self.query_one("#insp-detail", Static).update(self._detail(rec))

    def _detail(self, rec: DecisionRecord):
        from rich.console import Group

        d = rec.data
        card = card_from_record(rec)
        t = Text()
        t.append(
            f"Earth: {self.earth_label}   ·   planner {d['planner']}   ·   tick {rec.tick}\n", "dim"
        )
        q = {
            "hazard_triage": Q.HAZARD_TRIAGE,
            "instrument_choice": Q.INSTRUMENT_CHOICE,
        }.get(d["question"])
        if q:
            t.append("\nOPTIONS THE DECISION CHOSE FROM\n", "bold cyan")
            for name, desc in q.options.items():
                mark = "▶ " if name == d["choice"] else "  "
                t.append(f"{mark}{name}", "bold green" if name == d["choice"] else "")
                t.append(f" - {desc}\n", "dim")
        if d.get("ideal"):
            t.append(f"\noracle (hidden from the model): {d['ideal']}\n", "dim")
        t.append("\nSTATE SNAPSHOT\n", "bold cyan")
        t.append(json.dumps(rec.snapshot, indent=1))
        return Group(format_card(card, self.session.settings.confidence_threshold), t)

    def action_close(self) -> None:
        self.dismiss(None)


class ReportScreen(ModalScreen):
    BINDINGS = [Binding("escape", "close", "Close"), Binding("q", "app.quit", "Quit")]
    DEFAULT_CSS = """
    ReportScreen { align: center middle; }
    #report { width: 64; height: auto; border: round $accent; padding: 1 2; background: $surface; }
    """

    def __init__(self, session: GameSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        s = summarize(self.session.rover)
        recs = self.session.decisions
        local = sum(1 for r in recs if not r.data.get("escalated"))
        t = Text()
        t.append("MISSION REPORT\n\n", "bold")
        outcome = "Reached the science site" if s.reached_goal else f"Rover lost: {s.dead_reason}"
        t.append(outcome + "\n\n", "bold green" if s.reached_goal else "bold red")
        t.append(f"Distance        {s.progress_m} / 2000 m\n")
        t.append(f"Sols            {s.ticks / 1480:.2f}\n")
        t.append(f"Science         {s.science} ({s.samples} samples)\n")
        t.append(f"Wheels          {s.wheels}%\n")
        t.append(f"Decisions       {len(recs)} ({local} handled locally)\n")
        t.append(f"Score           {s.score}\n\n")
        t.append("Esc to keep looking around · Q to quit", "dim")
        yield Static(Panel(t, title="Roverthinking"), id="report")

    def action_close(self) -> None:
        self.dismiss(None)


class RemoveScreen(ModalScreen):
    """Pick an active or scheduled event to take away."""

    BINDINGS = [Binding("escape", "close", "Cancel"), Binding("x", "close", "Cancel")]
    DEFAULT_CSS = """
    RemoveScreen { align: center middle; }
    #remove { width: 62; height: auto; max-height: 24; border: round $accent; padding: 1 2;
              background: $surface; }
    """

    def __init__(self, session: GameSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        with Vertical(id="remove"):
            yield Static("Remove which event?  (Enter to remove, Esc to cancel)")
            yield OptionList(id="remove-options")

    def on_mount(self) -> None:
        r = self.session.rover
        ol = self.query_one("#remove-options", OptionList)
        for h in r.active_hazards():
            where = "" if h.x is None else f", {max(0, round(h.x - r.x))} m ahead"
            label = HAZARD_LABELS.get(h.kind, h.kind)
            ol.add_option(Option(f"#{h.id}  {label}  sev {h.severity}{where}", id=f"h:{h.id}"))
        for i, (at, kind, _, sev) in enumerate(r.schedule):
            ol.add_option(
                Option(
                    f"scheduled: {HAZARD_LABELS.get(kind, kind)} sev {sev or '?'} in "
                    f"{max(0, at - r.tick_n) / 10:.0f}s",
                    id=f"s:{i}",
                )
            )
        if ol.option_count:
            ol.add_option(Option("-- remove everything --", id="all"))
            ol.highlighted = 0
        else:
            self.query_one(Static).update("Nothing to remove. Esc to close.")
        ol.focus()

    def on_option_list_option_selected(self, ev: OptionList.OptionSelected) -> None:
        self.dismiss(ev.option.id)

    def action_close(self) -> None:
        self.dismiss(None)


class ScenarioPreviewScreen(ModalScreen):
    """A free-text scenario as Earth understood it. Nothing runs until the player confirms."""

    BINDINGS = [
        Binding("enter", "launch", "Launch"),
        Binding("s", "save", "Save and launch"),
        Binding("escape", "cancel", "Cancel"),
    ]
    DEFAULT_CSS = """
    ScenarioPreviewScreen { align: center middle; }
    #preview { width: 74; height: auto; max-height: 30; border: round $accent; padding: 1 2;
               background: $surface; }
    """

    def __init__(self, plan, text: str, earth_label: str) -> None:
        super().__init__()
        self.plan, self.text, self.earth_label = plan, text, earth_label

    def compose(self) -> ComposeResult:
        t = Text()
        t.append(f"{self.plan.title}\n", "bold")
        t.append(f"{self.plan.description}\n\n")
        t.append(f"You wrote: {self.text}\n", "dim")
        t.append(f"Read by: {self.earth_label}\n\n", "dim magenta")
        t.append("WHAT WILL HAPPEN\n", "bold cyan")
        for e in self.plan.events:
            sev = {1: "minor", 2: "major", 3: "catastrophic"}[e.severity]
            where = f", {e.ahead_m:.0f} m ahead" if e.ahead_m else ""
            label = HAZARD_LABELS.get(e.kind, e.kind)
            t.append(f"  +{e.after_seconds:>4.0f}s   {sev} {label.lower()}{where}\n")
        t.append("\nEnter: launch    S: save to scenarios/ and launch    Esc: cancel", "dim")
        yield Static(Panel(t, title="Scenario preview"), id="preview")

    def action_launch(self) -> None:
        self.dismiss("launch")

    def action_save(self) -> None:
        self.dismiss("save")

    def action_cancel(self) -> None:
        self.dismiss(None)
