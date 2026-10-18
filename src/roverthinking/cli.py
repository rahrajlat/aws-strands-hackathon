"""`roverthinking` command line."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from roverthinking import __version__
from roverthinking.config import EARTH_PROVIDERS, load_settings

app = typer.Typer(help="Roverthinking: Earth is too far. The rover decides.", no_args_is_help=True)

EarthOpt = Annotated[str | None, typer.Option("--earth", help=f"Earth provider: {'|'.join(EARTH_PROVIDERS)}")]


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def check(
    earth: EarthOpt = None,
    ping: Annotated[bool, typer.Option(help="make one real call to the Earth model")] = False,
) -> None:
    """Show the active configuration, and whether Decider and Earth are reachable."""
    from roverthinking.decisions.model import make_decider
    from roverthinking.earth.providers import make_earth

    s = load_settings(earth)
    typer.echo(f"Earth provider : {s.earth_provider} ({s.earth_model or 'no model set'})")
    typer.echo(f"Threshold      : {s.confidence_threshold}")
    _, note = make_decider(s.decider_mode, s.decider_url)
    typer.echo(f"Decider        : {note}")
    em, enote = make_earth(s)
    typer.echo(f"Earth          : {enote}")
    if ping and em.name != "mock":
        from roverthinking.sim.rover import Rover

        snap = Rover().snapshot()
        reply = em.answer_escalation(snap, "hazard_triage", ["slow", "reroute"], "slow", 0.5)
        typer.echo(
            f"Ping           : {reply.recommendation!r} in {reply.latency_ms:.0f} ms "
            f"({reply.tokens_in} in / {reply.tokens_out} out tokens)\n                 {reply.reasoning}"
        )


@app.command()
def play(
    planner: Annotated[str, typer.Option(help="rules | steward | llm_only")] = "steward",
    earth: EarthOpt = None,
    decider: Annotated[str | None, typer.Option(help="auto | server | mock")] = None,
    seed: int = 7,
    scenario: Annotated[Path | None, typer.Option(help="a scenario YAML to replay")] = None,
    ambient: Annotated[float, typer.Option(help="random hazard chance per tick (sandbox: 0)")] = 0.0,
    log_dir: Annotated[Path, typer.Option(help="where the JSONL decision log goes")] = Path("logs"),
) -> None:
    """Start the game."""
    from roverthinking.decisions.model import make_decider
    from roverthinking.earth.providers import make_earth
    from roverthinking.game.scenarios import load_scenario
    from roverthinking.game.session import GameSession
    from roverthinking.tui.app import RoverApp

    s = load_settings(earth, decider)
    sc = load_scenario(scenario) if scenario else None
    if sc:
        seed = sc.seed
    model, note = make_decider(s.decider_mode, s.decider_url)
    earth_model, _ = make_earth(s)
    session = GameSession(
        s,
        planner=planner,
        seed=seed,
        ambient=ambient,
        scenario=sc,
        decider=model,
        earth=earth_model,
        log_dir=log_dir,
    )
    em = earth_model
    # The display never names the backend/model — the rover is simply contacting Earth.
    if em.name == "mock":
        label = "Mission Control (link faint)"
        earth_line = "Earth link is faint — only limited guidance is getting through."
    else:
        label = "Mission Control"
        earth_line = "Earth link online — contacting intelligence from Earth."
    try:
        RoverApp(session, label, [note, earth_line]).run()
    finally:
        session.close()


@app.command()
def bench(
    crises: Annotated[int, typer.Option(help="number of seeded crises")] = 100,
    seed: int = 42,
    earth: EarthOpt = None,
) -> None:
    """Run the headless benchmark (not built yet)."""
    typer.echo("The benchmark arrives in Milestone 6.")
    raise typer.Exit(1)


@app.command()
def sim(
    seed: int = 42,
    planner: Annotated[str, typer.Option(help="rules | steward (offline mock decider)")] = "rules",
    rate: Annotated[float, typer.Option(help="random hazard chance per tick")] = 1 / 150,
) -> None:
    """Drive the rover headless (no screen) and print a summary."""
    from roverthinking.bench.run import run_headless

    r = run_headless(planner, seed, rate)
    s = r.summary
    outcome = "reached the goal" if s.reached_goal else (s.dead_reason or "ran out of time")
    typer.echo(f"planner={planner} seed={seed} hazards={r.hazards}: {outcome}")
    typer.echo(f"progress {s.progress_m} m in {s.ticks} ticks, score {s.score}")
    typer.echo(
        f"science {s.science} ({s.samples} samples), wheels {s.wheels}%, instruments "
        f"{s.instruments}%, shield charges left {s.shield_charges}"
    )
    typer.echo(f"decisions {r.decisions} ({r.correct} matched ideal), rejected {r.actions_rejected}")
