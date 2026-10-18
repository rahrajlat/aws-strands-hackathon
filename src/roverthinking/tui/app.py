"""The Roverthinking game screen."""

from __future__ import annotations

import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Static

from roverthinking.game.scenarios import (
    HAZARD_LABELS,
    QUICK_KEYS,
    parse_add,
    save_scenario,
)
from roverthinking.game.session import GameSession
from roverthinking.planners import PLANNERS
from roverthinking.tui.rover_view import RoverView
from roverthinking.tui.widgets.decision_panel import DecisionPanel
from roverthinking.tui.widgets.panels import DecisionFeed, EarthLinkPanel, Gauges, TopBar
from roverthinking.tui.widgets.screens import (
    InspectorScreen,
    RemoveScreen,
    ReportScreen,
    ScenarioPreviewScreen,
    WizardScreen,
)

HINT = (
    "[b]Add:[/] d dust · r rock · t sand · h slope · m meteors · n sensors · f flare · g rock-find"
    "   [b]s[/] wizard · [b]c[/] cascade · [b]x[/] remove · [b]/[/] order or /add /remove /clear"
)


class RoverApp(App):
    TITLE = "Roverthinking"
    AUTO_FOCUS = ""
    CSS = """
    #topbar { height: 1; background: #1c1c24; }
    #main { height: 1fr; }
    #view { width: 1fr; }
    #side { width: 58; border-left: solid #444455; }
    #decision { height: auto; max-height: 24; }
    #feed { height: 1fr; border-top: solid #444455; padding: 0 1; }
    #bottom { height: 8; }
    #gauges { width: 1fr; border-top: solid #444455; }
    #earth { width: 62; border-top: solid #444455; border-left: solid #444455; }
    #hint { height: 1; background: #1c1c24; color: #bbbbcc; }
    #cmd { height: 3; }
    """
    BINDINGS = [
        Binding("space", "pause", "Pause"),
        Binding("1", "speed(1)", "1x"),
        Binding("2", "speed(2)", "2x"),
        Binding("3", "speed(3)", "4x"),
        Binding("p", "planner", "Planner"),
        Binding("i", "inspector", "Inspector"),
        Binding("s", "wizard", "Wizard"),
        Binding("c", "cascade", "Cascade"),
        Binding("x", "remove", "Remove event"),
        Binding("w", "save", "Save scenario"),
        Binding("slash", "focus_cmd", "Order", show=True),
        Binding("escape", "blur_cmd", "", show=False),
        Binding("q", "quit", "Quit"),
        *[Binding(k, f"cast('{v}')", "", show=False) for k, v in QUICK_KEYS.items()],
    ]

    def __init__(self, session: GameSession, earth_label: str, notes: list[str] | None = None):
        super().__init__()
        self.session = session
        self.earth_label = earth_label
        self.notes = notes or []
        self._last = time.perf_counter()
        self._n = 0
        self._reported = False
        self._scenario_text = ""

    def compose(self) -> ComposeResult:
        yield TopBar(self.session, self.earth_label, id="topbar")
        with Horizontal(id="main"):
            yield RoverView(self.session, id="view")
            with Vertical(id="side"):
                yield DecisionPanel(self.session.settings.confidence_threshold, id="decision")
                yield DecisionFeed(self.session, id="feed")
        with Horizontal(id="bottom"):
            yield Gauges(self.session, id="gauges")
            yield EarthLinkPanel(self.session, self.earth_label, id="earth")
        yield Static(HINT, id="hint")
        yield Input(
            placeholder="order to the rover: stop · go slow · safe mode · sample drill    (or /help)",
            id="cmd",
        )
        yield Footer()

    def on_mount(self) -> None:
        def intro() -> None:
            feed = self.query_one(DecisionFeed)
            feed.write("[b]Roverthinking[/] - earth is too far. The rover decides.")
            feed.write("Throw hazards at it. Orders you type reach it after the light delay.")
            for n in self.notes:
                feed.write(f"[yellow]{n}[/]")

        self._view = self.query_one(RoverView)
        self._feed = self.query_one(DecisionFeed)
        self._decision = self.query_one(DecisionPanel)
        self._earth_panel = self.query_one(EarthLinkPanel)
        self._topbar = self.query_one(TopBar)
        self._gauges = self.query_one(Gauges)
        self.call_after_refresh(intro)
        self.set_interval(1 / 20, self._frame)

    # ---- the loop -----------------------------------------------------------------------------

    def _frame(self) -> None:
        if not self.is_running or not hasattr(self, "_view"):
            return
        now = time.perf_counter()
        dt = min(now - self._last, 0.1)
        self._last = now
        s = self.session
        events = s.advance(dt)
        if events:
            self._view.ingest(events)
            self._feed.add(events)
            self._decision.ingest(events)
        self._view.frame(dt)
        self._n += 1
        if self._n % 2 == 0:
            self._earth_panel.refresh_link(self._view.t)
        if self._n % 4 == 0:
            self._decision.redraw(self._view.t)
            self._topbar.refresh_bar()
            self._gauges.refresh_gauges()
        if s.ready_plan is not None:
            plan, s.ready_plan = s.ready_plan, None
            self._preview_scenario(plan)
        if s.rover.finished and not self._reported:
            self._reported = True
            self.push_screen(ReportScreen(s))

    # ---- actions ------------------------------------------------------------------------------

    def _modal(self, screen, callback=None) -> None:
        """Open a menu; the game pauses while it is open and resumes (if it was running) after."""
        was_paused = self.session.paused
        self.session.paused = True

        def done(result) -> None:
            self.session.paused = was_paused
            if callback is not None:
                callback(result)

        self.push_screen(screen, done)

    def note(self, text: str) -> None:
        self._feed.write(text)

    def _preview_scenario(self, plan) -> None:
        def done(choice) -> None:
            if not choice:
                self.note("[dim]scenario cancelled[/]")
                return
            sc = self.session.schedule_plan(plan)
            self.note(f"🎬 [b]Scenario launched:[/] {sc.name} ({len(sc.events)} events)")
            if choice == "save":
                self.note(f"💾 saved to [b]{save_scenario(sc)}[/]")

        self._modal(ScenarioPreviewScreen(plan, self._scenario_text, self.earth_label), done)

    def action_pause(self) -> None:
        self.session.toggle_pause()

    def action_speed(self, key: int) -> None:
        self.session.set_speed(int(key))

    def action_planner(self) -> None:
        if self.session.decider is None:
            self.notify("No decider available; only the rules planner can run.")
            return
        order = list(PLANNERS)
        nxt = order[(order.index(self.session.planner_name) + 1) % len(order)]
        self.session.set_planner(nxt)
        self.note(f"🧠 [b]Planner switched to {nxt.upper()}[/]")

    def action_cast(self, kind: str) -> None:
        self.session.cast(kind)
        self.note(f"🎲 [b]You throw:[/] {HAZARD_LABELS[kind]}")

    def action_cascade(self) -> None:
        sc = self.session.cascade()
        self.note("🌪 [b]Cascade:[/] " + " → ".join(e.describe() for e in sc.events))

    def action_wizard(self) -> None:
        def done(result) -> None:
            if result:
                ev, delay = result
                self.session.schedule(ev, delay)
                self.note(
                    f"🎲 [b]Scheduled:[/] {ev.describe()} ({'now' if not delay else f'in {delay:.0f}s'})"
                )

        self._modal(WizardScreen(self.session), done)

    def action_remove(self) -> None:
        def done(choice) -> None:
            if not choice:
                return
            if choice == "all":
                self.note(f"🧹 [b]Cleared[/] {self.session.clear_hazards()} event(s)")
            elif choice.startswith("h:"):
                hid = int(choice[2:])
                self.note(
                    f"🧹 [b]Removed[/] event #{hid}"
                    if self.session.remove_hazard(hid)
                    else f"[red]event #{hid} is already gone[/]"
                )
            elif choice.startswith("s:"):
                self.session.rover.cancel_scheduled(int(choice[2:]))
                self.note("🧹 [b]Cancelled[/] a scheduled event")

        self._modal(RemoveScreen(self.session), done)

    def action_inspector(self) -> None:
        self._modal(InspectorScreen(self.session, self.earth_label))

    def action_save(self) -> None:
        name = f"{self.session.scenario_name}-seed{self.session.seed}"
        path = save_scenario(self.session.recorded_scenario(name))
        self.note(f"💾 saved scenario to [b]{path}[/]")

    def action_focus_cmd(self) -> None:
        self.query_one("#cmd", Input).focus()

    def action_blur_cmd(self) -> None:
        self.set_focus(None)

    def on_input_submitted(self, ev: Input.Submitted) -> None:
        text = ev.value.strip()
        ev.input.value = ""
        self.set_focus(None)
        if not text:
            return
        if text.startswith("/"):
            self._slash(text)
            return
        err = self.session.send_order(text)
        if err:
            self.note(f"⛔ [red]{err}[/]")

    def _slash(self, text: str) -> None:
        cmd, _, rest = text.partition(" ")
        if cmd == "/help":
            self.note(
                "Orders: stop · go [slow|normal] · slow · safe mode · shield · shield down · "
                "back up · sample <camera|spectrometer|drill|skip>"
            )
            self.note("Events: /add <hazard> [1-3] [metres ahead] · /remove <id> · /clear · x menu")
            self.note("Other: /save · /scenario <text> (free text: coming soon)")
        elif cmd == "/add":
            try:
                ev = parse_add(rest)
            except ValueError as exc:
                self.note(f"[red]{exc}[/]")
                return
            self.session.cast(ev.kind, ev.ahead, ev.severity)
            self.note(f"🎲 [b]You add:[/] {ev.describe()}")
        elif cmd == "/remove":
            if not rest.strip().lstrip("#").isdigit():
                self.note("[red]/remove needs an event number, e.g. /remove 3 (see the Events line)[/]")
                return
            hid = int(rest.strip().lstrip("#"))
            self.note(
                f"🧹 [b]Removed[/] event #{hid}"
                if self.session.remove_hazard(hid)
                else f"[red]no active event #{hid}[/]"
            )
        elif cmd == "/clear":
            self.note(f"🧹 [b]Cleared[/] {self.session.clear_hazards()} event(s)")
        elif cmd == "/save":
            self.action_save()
        elif cmd == "/scenario":
            if not rest.strip():
                self.note("[red]describe it, e.g. /scenario a meteor shower, then a sand trap[/]")
                return
            self._scenario_text = rest.strip()
            self.session.plan_scenario(rest.strip())
            self.note(f"🧠 [b]{self.earth_label}[/] is turning that into a scenario...")
        else:
            self.note(f"[red]unknown command {cmd}[/] (try /help)")
