#!/usr/bin/env python3
"""Framework demo TUI — three scenes, auto-advance every 8 s.

Run:  python demo.py   (or: uv run python demo.py)
Keys: SPACE / ENTER = advance · Q = quit
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from rich.syntax import Syntax
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import ContentSwitcher, RichLog, Static

_ROOT = Path(__file__).resolve().parent
_YAML = (_ROOT / "recipes" / "airflow-task-restart" / "airflow_task_restart.yaml").read_text()

_CODE = """\
from decider_framework import DecisionEngine

# load — questions + policy, nothing else
engine = DecisionEngine.from_yaml("airflow_task_restart.yaml")

result = engine.decide(
    model=decider_2b,
    state={
        "task":          "load_clickstream",
        "dag":           "daily_etl",
        "error":         "S3Exception: SlowDown",
        "retries":       1,
        "max_retries":   3,
        "critical_path": False,
    },
)

print(result.action)      # "restart"
print(result.confidence)  # 0.87
print(result.escalated)   # False
"""

_CASES = [
    ("ingest_clickstream",   "daily_etl",        "S3Exception: SlowDown — request throttled",           1, 3, False, "restart",  87),
    ("transform_orders",     "daily_etl",        "AnalysisException: cannot resolve 'order_uuid'",      3, 3, True,  "escalate", 91),
    ("aggregate_metrics",    "hourly_reporting", "GlueException: Worker OOM (G.1X DPU)",                0, 2, False, "restart",  79),
    ("load_to_redshift",     "daily_etl",        "AccessDeniedException: glue-etl-role not authorized", 2, 3, True,  "escalate", 88),
    ("crawl_raw_zone",       "daily_etl",        "CrawlerException: Data Catalog throttled",            0, 2, False, "restart",  82),
    ("join_customer_events", "daily_etl",        "GlueException: Internal service error (HTTP 500)",    1, 3, True,  None,       58),
]

_DOTS = ("● ○ ○", "○ ● ○", "○ ○ ●")
_LABELS = (
    "1 / 3  ·  YAML Config  ·  questions + policy, nothing else",
    "2 / 3  ·  Python API   ·  two lines to wire it up",
    "3 / 3  ·  Live Decisions  ·  six Glue failures · Decider 2B · ~115 ms each",
)

CSS = """
Screen { background: #0f1117; color: #e2e8f0; }

#scene-bar {
    height: 3;
    background: #13151f;
    border-bottom: solid #1e2235;
    padding: 0 3;
    align: left middle;
}

ContentSwitcher { height: 1fr; background: #0f1117; }

#scene-0, #scene-1 {
    layout: horizontal;
    height: 1fr;
    padding: 1 2;
}

#scene-2 { height: 1fr; padding: 1 2; }

.left  { width: 34%; padding-right: 2; height: 1fr; }
.right { width: 1fr; border-left: solid #1e2235; padding-left: 2; height: 1fr; overflow-y: auto; }

.h { text-style: bold; margin-bottom: 1; }
.dim { color: #475569; }
.accent { color: #7c5af5; text-style: bold; }

#decisions-log {
    height: 1fr;
    background: #0d1117;
    border: solid #1e2235;
    padding: 1 2;
    margin-top: 1;
}

#footer {
    height: 3;
    background: #13151f;
    border-top: solid #1e2235;
    padding: 0 3;
    align: left middle;
    color: #334155;
}
"""


class DemoApp(App):
    CSS = CSS
    BINDINGS = [("n", "advance", "Next"), ("q", "quit", "Quit")]

    scene: reactive[int] = reactive(0)

    # ── compose ──────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Container(Static(id="label"), id="scene-bar")
        with ContentSwitcher(initial="scene-0"):
            yield self._scene_yaml()
            yield self._scene_code()
            yield self._scene_decisions()
        yield Container(Static("N  next scene  ·  Q  quit"), id="footer")

    def _scene_yaml(self) -> Horizontal:
        syn = Syntax(_YAML, "yaml", theme="monokai", line_numbers=False)
        return Horizontal(
            Vertical(
                Static("[bold]The Decision Config[/bold]\n", classes="h"),
                Static("All logic lives in YAML.\nThree yes/no questions\nand a policy. No code.\n"),
                Static("[#7c5af5]is_transient[/#7c5af5]      transient failure?"),
                Static("[#7c5af5]retries_exhausted[/#7c5af5]  used all retries?"),
                Static("[#7c5af5]critical_path[/#7c5af5]      SLA at risk?\n"),
                Static("[dim]Drop in any YAML.\nSame engine.[/dim]", classes="dim"),
                classes="left",
            ),
            Container(Static(syn), classes="right"),
            id="scene-0",
        )

    def _scene_code(self) -> Horizontal:
        syn = Syntax(_CODE, "python", theme="monokai", line_numbers=True)
        return Horizontal(
            Vertical(
                Static("[bold]Two Lines of Python[/bold]\n", classes="h"),
                Static("Load the YAML.\nCall decide().\n"),
                Static("Returns:"),
                Static("[green]result.action[/green]       restart | skip | escalate"),
                Static("[#94a3b8]result.confidence[/#94a3b8]   0.87"),
                Static("[#94a3b8]result.escalated[/#94a3b8]    False\n"),
                Static("[dim]Any Decider-compatible\nmodel. Any domain.[/dim]", classes="dim"),
                classes="left",
            ),
            Container(Static(syn), classes="right"),
            id="scene-1",
        )

    def _scene_decisions(self) -> Vertical:
        return Vertical(
            Static("[bold]Live Decisions[/bold]  [dim]Decider 2B · six Glue failures · no server needed[/dim]"),
            RichLog(id="decisions-log", markup=True, highlight=False, auto_scroll=True),
            id="scene-2",
        )

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._refresh_label()

    def watch_scene(self, value: int) -> None:
        self.query_one(ContentSwitcher).current = f"scene-{value}"
        self._refresh_label()
        if value == 2:
            self._play_decisions()

    def _refresh_label(self) -> None:
        i = self.scene
        self.query_one("#label", Static).update(f"[dim]{_DOTS[i]}[/dim]  [bold]{_LABELS[i]}[/bold]")

    # ── actions ───────────────────────────────────────────────────────────────

    def action_advance(self) -> None:
        if self.scene < 2:
            self.scene += 1
        else:
            self.exit()

    # ── workers ───────────────────────────────────────────────────────────────

    @work
    async def _play_decisions(self) -> None:
        log = self.query_one("#decisions-log", RichLog)
        log.clear()
        await asyncio.sleep(0.4)
        for task, dag, error, ret, mx, crit, action, conf in _CASES:
            log.write(f"[dim]task:[/dim]     [bold]{task}[/bold]  [dim][{dag}][/dim]")
            log.write(f"[dim]error:[/dim]    {error}")
            log.write(f"[dim]retries:[/dim]  {ret}/{mx}  critical={crit}")
            if action == "restart":
                log.write(f"[dim]decision:[/dim] [bold green]restart[/bold green]  [dim]Decider 2B · {conf}% · ~115 ms[/dim]\n")
            elif action == "escalate":
                log.write(f"[dim]decision:[/dim] [bold red]escalate[/bold red]  [dim]Decider 2B · {conf}% · ~115 ms[/dim]\n")
            else:
                log.write(f"[dim]decision:[/dim] [yellow]unsure ({conf}%)[/yellow] [dim]— asking frontier model …[/dim]")
                await asyncio.sleep(0.45)
                log.write(f"[dim]decision:[/dim] [bold red]escalate[/bold red]  [dim]Amazon Bedrock[/dim]\n")
            await asyncio.sleep(0.32)


if __name__ == "__main__":
    DemoApp().run()
