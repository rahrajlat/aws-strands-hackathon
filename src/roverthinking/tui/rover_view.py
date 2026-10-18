"""The animated rover view: a Textual widget that paints the scene's canvas."""

from __future__ import annotations

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

from roverthinking.game.session import GameSession
from roverthinking.sim.rover import SimEvent
from roverthinking.tui.canvas import Canvas
from roverthinking.tui.effects import EffectsLayer
from roverthinking.tui.scene import SceneRenderer


class RoverView(Widget):
    DEFAULT_CSS = "RoverView { background: #000000; }"

    def __init__(self, session: GameSession, **kw) -> None:
        super().__init__(**kw)
        self.session = session
        self.scene = SceneRenderer()
        self.fx = EffectsLayer()
        self.canvas: Canvas | None = None
        self.t = 0.0
        self._styles: dict[tuple, Style] = {}

    def ingest(self, events: list[SimEvent]) -> None:
        self.fx.ingest(events, self.session.rover)

    def frame(self, dt: float) -> None:
        w, h = self.size
        if w < 30 or h < 10:
            return
        s = self.session
        if not s.paused:
            self.t += dt
        self.fx.update(0.0 if s.paused else dt, s.rover, w, h, self.t)
        self.canvas = self.scene.render(s.rover, w, h, s._acc, self.t, self.fx)
        self.refresh()

    def _style(self, fg, bg) -> Style:
        key = (fg, bg)
        st = self._styles.get(key)
        if st is None:
            st = Style(color=Color.from_rgb(*fg), bgcolor=Color.from_rgb(*bg))
            if len(self._styles) > 4000:
                self._styles.clear()
            self._styles[key] = st
        return st

    def render_line(self, y: int) -> Strip:
        c, width = self.canvas, self.size.width
        if c is None or c.w != width or y >= c.h:
            return Strip.blank(width)
        dx, dy = self.fx.shake_offset(self.t)
        yy = min(max(y + dy, 0), c.h - 1)
        chars, fgs, bgs = c.ch[yy], c.fg[yy], c.bg[yy]
        segs: list[Segment] = []
        run, key = [], None
        for x in range(width):
            xx = min(max(x + dx, 0), width - 1)
            k = (tuple(fgs[xx]), tuple(bgs[xx]))
            if k != key and run:
                segs.append(Segment("".join(run), self._style(*key)))
                run = []
            key = k
            run.append(chars[xx])
        if run:
            segs.append(Segment("".join(run), self._style(*key)))
        return Strip(segs, width)
