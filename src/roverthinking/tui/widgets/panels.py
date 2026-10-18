"""Top bar, gauges, Earth link and decision feed."""

from __future__ import annotations

import random

from rich.text import Text
from textual.widgets import RichLog, Static

from roverthinking.earth.link import TO_EARTH
from roverthinking.game.session import GameSession
from roverthinking.sim.rover import GOAL_M, SHIELD_CHARGES, SimEvent

SPARK = "▁▂▃▄▅▆▇█"


def bar(frac: float, width: int, color: str) -> Text:
    frac = max(0.0, min(1.0, frac))
    n = round(frac * width)
    t = Text()
    t.append("█" * n, style=color)
    t.append("░" * (width - n), style="grey35")
    return t


def spark(values, lo: float, hi: float, width: int) -> str:
    vals = list(values)[-width:]
    span = max(hi - lo, 1e-9)
    out = "".join(SPARK[min(7, max(0, int((v - lo) / span * 7.99)))] for v in vals)
    return out.rjust(width)


def level_color(frac: float) -> str:
    return "green" if frac > 0.5 else ("yellow" if frac > 0.25 else "red")


class TopBar(Static):
    def __init__(self, session: GameSession, earth_label: str, **kw) -> None:
        super().__init__(**kw)
        self.session = session
        self.earth_label = earth_label

    def refresh_bar(self) -> None:
        s, r = self.session, self.session.rover
        hh, mm = int(r.hour), int((r.hour % 1) * 60)
        t = Text()
        t.append(" ● ROVERTHINKING ", style="bold white on #b3261e")
        t.append(f"  Sol {r.sol} · {hh:02d}:{mm:02d} MTC", style="bold")
        t.append(f"  · Earth delay {s.delay_label}", style="cyan")
        t.append(f"  · Planner: {s.planner_name.upper()}", style="bold yellow")
        t.append(f"  · Earth: {self.earth_label}", style="magenta")
        t.append(f"  · Score {s.score}", style="bold green")
        state = "PAUSED" if s.paused else f"{s.speed}x"
        t.append(f"  · {state}", style="bold red" if s.paused else "dim")
        if r.finished:
            t.append(
                "  · " + ("MISSION COMPLETE" if r.mode == "done" else "ROVER LOST"),
                style="bold red" if r.mode == "dead" else "bold green",
            )
        self.update(t)


class Gauges(Static):
    def __init__(self, session: GameSession, **kw) -> None:
        super().__init__(**kw)
        self.session = session

    def refresh_gauges(self) -> None:
        s, r = self.session, self.session.rover
        width = max(self.size.width, 60)
        cw = width // 5
        inner = cw - 2
        h = s.history
        shield_frac = r.shield_charges / SHIELD_CHARGES
        cols = [
            ("SPEED", f"{r.velocity:.1f}", r.velocity / 2.0, "cyan", h["speed"], 0, 2),
            ("WHEELS", f"{r.wheels:.0f}%", r.wheels / 100, level_color(r.wheels / 100), h["wheels"], 0, 100),
            (
                "INSTR",
                f"{r.instruments:.0f}%",
                r.instruments / 100,
                level_color(r.instruments / 100),
                h["instruments"],
                0,
                100,
            ),
            ("NAV CAM", f"{r.nav:.0f}%", r.nav / 100, level_color(r.nav / 100), h["nav"], 0, 100),
            (
                "SHIELD",
                "●" * r.shield_charges + "○" * (SHIELD_CHARGES - r.shield_charges),
                shield_frac,
                "cyan" if r.shield_charges else "red",
                None,
                0,
                1,
            ),
        ]
        out = Text()
        out.append(" TRAVERSE ", style="bold")
        out.append_text(bar(r.progress, max(10, width - 40), "green"))
        out.append(f" {min(r.x, GOAL_M):5.0f}/{GOAL_M:.0f} m  samples {r.samples} · science {r.science}\n")
        for row in range(3):
            line = Text()
            for name, val, frac, color, hist, lo, hi in cols:
                if row == 0:
                    line.append(f" {name:<8}{val:>{max(cw - 10, 1)}} "[:cw], style="bold")
                elif row == 1:
                    line.append(" ")
                    line.append_text(bar(frac, inner, color))
                    line.append(" ")
                else:
                    txt = spark(hist, lo, hi, inner) if hist is not None else ""
                    if hist is None:
                        txt = (
                            {
                                "off": "ready",
                                "deploying": "RAISING...",
                                "up": "SHIELD UP",
                                "retracting": "lowering...",
                            }[r.shield]
                            if r.shield_charges or r.shield != "off"
                            else "no charges"
                        )
                    line.append(" " + txt.ljust(inner) + " ", style=color)
            out.append_text(line)
            out.append("\n")
        hz = [f"#{x.id} {x.kind.replace('_', ' ')} s{x.severity}" for x in r.active_hazards()]
        sched = [f"{k.replace('_', ' ')} in {max(0, t - r.tick_n) / 10:.0f}s" for t, k, _, _ in r.schedule]
        txt = Text(" Events: ", style="bold")
        txt.append(", ".join(hz) if hz else "none", style="yellow" if hz else "dim")
        if sched:
            txt.append("   Scheduled: " + ", ".join(sched), style="dim")
        out.append_text(txt)
        self.update(out)


class EarthLinkPanel(Static):
    def __init__(self, session: GameSession, earth_label: str, **kw) -> None:
        super().__init__(**kw)
        self.session = session
        self.earth_label = earth_label

    def refresh_link(self, t: float) -> None:
        s, r = self.session, self.session.rover
        width = max(self.size.width - 4, 30)
        length = width - 16
        cells = ["─"] * length
        marks: dict[int, str] = {}
        flight = s.link.in_flight()
        for m in flight:
            p = m.progress(s.now)
            pos = int(p * (length - 2))
            if m.direction != TO_EARTH:
                pos = (length - 2) - pos
            marks[pos] = "📨" if m.direction == TO_EARTH else "📩"
        line = Text(" ROVER ", style="bold")
        i = 0
        while i < length:
            if i in marks:
                line.append(marks[i])
                i += 2
            else:
                if r.comms_ok:
                    line.append(cells[i], style="cyan")
                else:
                    line.append(random.choice("░▒▓╳ "), style="red")
                i += 1
        line.append(" EARTH", style="bold blue")
        out = Text()
        out.append(" EARTH LINK ", style="bold")
        if r.comms_ok:
            out.append(f"UP · one-way {s.delay_label}\n", style="green")
        else:
            out.append("DOWN · static on the line\n", style="bold red")
        out.append_text(line)
        out.append("\n")
        if flight:
            for m in sorted(flight, key=lambda m: m.arrive_at)[:3]:
                arrow = "→ Earth" if m.direction == TO_EARTH else "→ Rover"
                out.append(f" {arrow}: {m.text[: width - 22]}  ETA {s.eta_label(m)}\n", style="yellow")
        else:
            out.append(" nothing in flight\n", style="dim")
        out.append(f" Earth: {self.earth_label}", style="dim magenta")
        self.update(out)


class DecisionFeed(RichLog):
    def __init__(self, session: GameSession, **kw) -> None:
        super().__init__(markup=True, wrap=True, min_width=20, max_lines=400, **kw)
        self.session = session

    def stamp(self, e: SimEvent) -> str:
        r = self.session.rover
        mins = (e.tick + r._offset) % 1480 / 1480 * 24
        return f"[dim]S{(e.tick + r._offset) // 1480 + 1} {int(mins):02d}:{int(mins % 1 * 60):02d}[/]"

    def add(self, events: list[SimEvent]) -> None:
        for e in events:
            line = self.format(e)
            if line:
                self.write(f"{self.stamp(e)} {line}")

    def format(self, e: SimEvent) -> str:  # noqa: C901
        d, k = e.data, e.kind
        if k == "decision":
            conf = d["confidence"]
            col = "green" if conf >= 0.7 else "yellow"
            b = "█" * round(conf * 8) + "░" * (8 - round(conf * 8))
            what = "order" if d["hazard"] == "order" else str(d["hazard"]).replace("_", " ")
            lat = f" · {d['latency_ms']:.0f}ms" if d.get("latency_ms") else ""
            if d.get("escalated"):
                return (
                    f"📡 [yellow]asking Earth[/] about {what} [dim](local: {d['choice']} {conf:.2f}{lat})[/]"
                )
            who = d.get("source") or d["planner"]
            if who == "strands-decider-2b":
                who = "decider"
            tag = " [yellow]acted under uncertainty[/]" if d.get("under_uncertainty") else ""
            if d.get("late"):
                tag = " [dim](hazard already passed)[/]"
            return f"⚡ [bold]{d['choice']}[/] [{col}]{b} {conf:.2f}[/] [dim]{what} · {who}{lat}[/]{tag}"
        if k == "alarm":
            where = "" if d["reason"] == "detected" else f" ({d['reason']})"
            return f"⚠ [red]{d['hazard'].replace('_', ' ').upper()}[/] severity {d['severity']}{where}"
        if k == "action_rejected":
            return f"✗ [red]{d['reason']}[/]"
        if k == "hazard_end":
            return f"✓ [green]{d['hazard'].replace('_', ' ')} {d['how']}[/]"
        if k == "damage":
            return f"💥 [red]-{d['amount']:.0f} {d['part']}[/] [dim]{d['cause']}[/]"
        if k == "stuck":
            return f"🛑 [red]stuck:[/] {d['why']}"
        if k == "freed":
            return "🟢 [green]rover is free[/]"
        if k == "sample_done":
            return f"🔬 [cyan]{d['instrument']} on {d['rock']}[/]: [bold]+{d['points']}[/] science"
        if k == "msg_sent":
            who = "Rover" if d["direction"] == TO_EARTH else "Earth"
            return f"📨 [yellow]{who} sends[/] {d['kind']}: {d['text']}"
        if k == "msg_received":
            who = "Earth" if d["direction"] == TO_EARTH else "Rover"
            return f"📩 [yellow]{who} receives[/] {d['kind']}: {d['text']}"
        if k == "order_result":
            return ("✅ " if d["ok"] else "⛔ ") + (
                f"order [bold]{d['text']}[/] executed"
                if d["ok"]
                else f"order [bold]{d['text']}[/] rejected: {d['reason']}"
            )
        if k == "order_held":
            return f"⚠ [bold yellow]ORDER HELD[/] '{d['text']}': {d['reason']}"
        if k == "reply_stale":
            return f"⚠ [yellow]Earth's reply is stale:[/] {d['reason']}"
        if k == "reply_timeout":
            return "⌛ [yellow]no reply from Earth; acting on the local choice[/]"
        if k == "order_interpreting":
            return f"🧠 [magenta]{d['earth']} is reading your order[/] [dim]'{d['text']}'[/]"
        if k == "order_interpreted":
            return f"🧠 understood as [bold]{d['order']}[/] [dim]{d.get('note', '')}[/]"
        if k == "scenario_planning":
            return f"🧠 [magenta]{d['earth']} is planning your scenario...[/]"
        if k == "scenario_failed":
            return f"⛔ [red]{d['reason']}[/]"
        if k == "decider_error":
            return f"⚠ [yellow]decider call failed ({d['error']}); using the rules table[/]"
        if k == "order_invalid":
            return f"⛔ [red]{d['reason']}[/]"
        if k == "safe_mode_enter":
            return "🔧 [cyan]safe mode[/]"
        if k == "shield_deploy":
            return f"🛡 [bold cyan]shield deploying[/] [dim]({d['charges_left']} charge(s) left)[/]"
        if k == "shield_up":
            return "🛡 [cyan]shield is up[/]"
        if k == "shield_down":
            return "🛡 [dim]shield lowered[/]"
        if k == "impact":
            return ""  # visual only; a hit that gets through also logs its damage
        if k == "rover_dead":
            return f"☠ [bold red]ROVER LOST: {d['reason']}[/]"
        if k == "goal_reached":
            return "🏁 [bold green]science site reached![/]"
        if k == "hazard_start" and d["hazard"] in (
            "dust_storm",
            "solar_flare",
            "meteor_shower",
            "sensor_fault",
        ):
            return None
        return ""
