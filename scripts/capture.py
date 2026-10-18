"""Headless screenshots of the game for the README and article (SVG, optionally PNG).

uv run python scripts/capture.py            # writes docs/assets/frame-*.svg
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from roverthinking.config import load_settings
from roverthinking.game.session import GameSession
from roverthinking.tui.app import RoverApp

OUT = Path("docs/assets")
SHOTS = [
    ("cruise", [], 1.5),
    ("rockfall", ["r"], 2.5),
    ("dust", ["d"], 4.0),
    ("flare", ["f"], 1.2),
]


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    session = GameSession(load_settings(earth="mock"), seed=7)
    app = RoverApp(session, "mock")
    async with app.run_test(size=(150, 46)) as pilot:
        await pilot.press("3")
        for name, keys, wait in SHOTS:
            if keys:
                await pilot.press(*keys)
            await pilot.pause(wait)
            path = OUT / f"frame-{name}.svg"
            path.write_text(app.export_screenshot(title="Roverthinking"))
            print("wrote", path)


if __name__ == "__main__":
    asyncio.run(main())
