import asyncio

import pytest

from roverthinking.config import load_settings
from roverthinking.game.session import GameSession
from roverthinking.tui import sprites
from roverthinking.tui.app import RoverApp
from roverthinking.tui.effects import EffectsLayer
from roverthinking.tui.scene import SceneRenderer


def test_sprites_are_rectangular():
    for rows in (sprites.STATION,):
        assert len({len(r) for r in rows}) == 1


@pytest.mark.parametrize(
    "kind",
    [
        "dust_storm",
        "rockfall",
        "sand_trap",
        "steep_slope",
        "meteor_shower",
        "sensor_fault",
        "solar_flare",
        "strange_rock",
    ],
)
def test_every_hazard_renders_at_several_sizes(kind):
    s = GameSession(load_settings(earth="mock"), seed=1)
    fx, scene = EffectsLayer(), SceneRenderer()
    s.cast(kind, ahead=80 if kind in ("rockfall", "sand_trap", "steep_slope", "strange_rock") else None)
    for _ in range(60):
        ev = s.advance(0.1)
        fx.ingest(ev, s.rover)
        fx.update(0.1, s.rover, 100, 30, s.rover.tick_n / 10)
    for w, h in ((140, 36), (80, 20), (40, 12)):
        frame = scene.render(s.rover, w, h, 0.3, 1.0, fx)
        assert len(frame.plain().splitlines()) == h


def test_app_runs_and_survives_key_mashing():
    async def go():
        session = GameSession(load_settings(earth="mock"), seed=3)
        app = RoverApp(session, "mock")
        async with app.run_test(size=(140, 45)) as pilot:
            for key in "drtgmnfhc":
                await pilot.press(key)
            await pilot.press("3")
            await pilot.pause(0.6)
            await pilot.press("s")
            await pilot.press("enter", "enter", "enter", "enter", "enter")
            await pilot.press("i")
            await pilot.pause(0.1)
            await pilot.press("escape")
            await pilot.press("slash")
            await pilot.press(*"stop", "enter")
            await pilot.press("space")
            await pilot.pause(0.2)
        return session

    session = asyncio.run(go())
    assert session.rover.tick_n > 0
    assert session.decisions


def test_hazard_particles_stop_at_the_shield_dome():
    from roverthinking.sim.rover import Rover
    from roverthinking.tui.effects import DUST, EffectsLayer, Particle

    fx = EffectsLayer()
    fx.dome = (40, 20, 20, 10)  # centre column, ground row, radii
    inside = Particle(40.0, 15.0, 0.0, 0.0, ":", DUST, 5.0, "screen")
    outside = Particle(5.0, 15.0, 0.0, 0.0, ":", DUST, 5.0, "screen")
    above = Particle(40.0, 2.0, 0.0, 0.0, ":", DUST, 5.0, "screen")
    fx.particles = [inside, outside, above]
    fx.update(0.05, Rover(seed=1), 100, 30, 0.0)
    assert inside not in fx.particles
    assert outside in fx.particles and above in fx.particles
    assert fx.shield_hit > 0  # the dome flashes where it was struck
    assert any(p.ch == "*" for p in fx.particles)  # and throws off a spark
