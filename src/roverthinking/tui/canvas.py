"""A character/colour grid. No Textual imports, so frames can be rendered in tests."""

from __future__ import annotations

RGB = tuple[int, int, int]


def lerp(a: RGB, b: RGB, t: float) -> RGB:
    t = max(0.0, min(1.0, t))
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


class Canvas:
    def __init__(self, w: int, h: int, bg: RGB = (0, 0, 0)) -> None:
        self.w, self.h = w, h
        self.ch = [[" "] * w for _ in range(h)]
        self.fg: list[list[RGB]] = [[(255, 255, 255)] * w for _ in range(h)]
        self.bg: list[list[RGB]] = [[bg] * w for _ in range(h)]
        # a pixel layer at twice the vertical resolution: each cell holds two stacked pixels
        self.pix: list[list[RGB | None]] = [[None] * w for _ in range(2 * h)]

    def put(self, x: int, y: int, ch: str, fg: RGB | None = None, bg: RGB | None = None) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            self.ch[y][x] = ch
            if fg is not None:
                self.fg[y][x] = fg
            if bg is not None:
                self.bg[y][x] = bg

    def text(self, x: int, y: int, s: str, fg: RGB, bg: RGB | None = None) -> None:
        for i, c in enumerate(s):
            if c != " ":
                self.put(x + i, y, c, fg, bg)

    def sprite(self, x: int, y: int, rows: list[str], color, transparent: str = " ") -> None:
        """Blit rows; `color` is an RGB or a function (ch, row, col) -> RGB | None."""
        for j, row in enumerate(rows):
            for i, c in enumerate(row):
                if c == transparent:
                    continue
                col = color(c, j, i) if callable(color) else color
                if col is not None:
                    self.put(x + i, y + j, c, col)

    def px(self, x: int, y: int, color: RGB) -> None:
        if 0 <= x < self.w and 0 <= y < 2 * self.h:
            self.pix[y][x] = color

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: RGB) -> None:
        """Fill pixels x0..x1, y0..y1 (inclusive)."""
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.px(x, y, color)

    def flush_pixels(self) -> None:
        """Composite the pixel layer into the cells using half-block characters."""
        for cy in range(self.h):
            top_row, bot_row = self.pix[2 * cy], self.pix[2 * cy + 1]
            for x in range(self.w):
                top, bot = top_row[x], bot_row[x]
                if top is None and bot is None:
                    continue
                if top is not None and bot is not None:
                    if top == bot:
                        self.put(x, cy, "█", top)
                    else:
                        self.put(x, cy, "▀", top, bot)
                elif top is not None:
                    self.put(x, cy, "▀", top, self.bg[cy][x])
                else:
                    self.put(x, cy, "▄", bot, self.bg[cy][x])
        self.pix = [[None] * self.w for _ in range(2 * self.h)]

    def tint(self, color: RGB, alpha: float) -> None:
        if alpha <= 0.01:
            return
        for y in range(self.h):
            fr, br = self.fg[y], self.bg[y]
            for x in range(self.w):
                fr[x] = lerp(fr[x], color, alpha)
                br[x] = lerp(br[x], color, alpha)

    def plain(self) -> str:
        return "\n".join("".join(r).rstrip() for r in self.ch)
