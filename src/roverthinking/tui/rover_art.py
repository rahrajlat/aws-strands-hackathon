"""Draws Rovy using Unicode box-drawing characters and symbols.

No pixel layer is used — everything goes through canvas.put() directly.
The rover is 14 chars wide × 7 text rows tall:

  row 0   │   ┌──────┐   │  head outline
  row 1   │   │  ·   │   │  OLED indicator
  row 2   │   │ ◉  ◉ │   │  eyes (expression changes with mood)
  row 3   │ ╔══╧══════╧══╗ │  chassis top, joining the head
  row 4   │ ║    ROVY   ·║ │  chassis body with name and status LED
  row 5   │ ╚══╤═══╤═══╤╝ │  chassis bottom with three strut points
  row 6   │    ◔   ◔   ◔  │  wheels (spin animated)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from roverthinking.tui import sprites as S
from roverthinking.tui.canvas import RGB, Canvas, lerp

# ── Palette ────────────────────────────────────────────────────────────────

HEAD_BORDER: RGB = (180, 186, 202)
HEAD_PLATE:  RGB = (24, 26, 34)
CHASSIS:     RGB = (14, 16, 22)
CHASSIS_HI:  RGB = (38, 42, 54)
NAME_GOLD:   RGB = (218, 172, 62)
STRUT:       RGB = (100, 106, 120)
WHEEL:       RGB = (30, 30, 40)
WHEEL_HI:    RGB = (78, 80, 94)

LED_COLORS: dict[str, RGB] = {
    "normal":    (70, 255, 100),
    "blink":     (70, 255, 100),
    "thinking":  (90, 220, 255),
    "squint":    (90, 220, 255),
    "happy":     (120, 255, 150),
    "alert":     (255, 196, 70),
    "surprised": (255, 226, 140),
    "hurt":      (255, 80, 70),
    "dizzy":     (255, 80, 70),
    "dead":      (60, 70, 62),
    "sleepy":    (70, 120, 180),
}

# eye character per mood
_EYE: dict[str, str] = {
    "normal":    "◉",
    "blink":     "─",
    "happy":     "◉",
    "alert":     "◈",
    "thinking":  "◎",
    "hurt":      "✕",
    "dizzy":     "@",
    "dead":      "×",
    "sleepy":    "◡",
    "squint":    "◔",
    "surprised": "⊙",
}

# eye colour per mood
_EYE_COL: dict[str, RGB] = {
    "normal":    (110, 220, 255),
    "blink":     (70, 155, 188),
    "happy":     (130, 255, 170),
    "alert":     (255, 196, 70),
    "thinking":  (120, 200, 255),
    "hurt":      (255, 85, 75),
    "dizzy":     (255, 85, 75),
    "dead":      (90, 82, 80),
    "sleepy":    (65, 115, 172),
    "squint":    (95, 180, 215),
    "surprised": (255, 235, 150),
}

# OLED single-char icon per mood  (None = animated)
_OLED_ICON: dict[str, str | None] = {
    "normal":    "·",
    "blink":     "·",
    "happy":     "♥",
    "alert":     "▲",
    "thinking":  None,   # cycles · • ●
    "hurt":      "×",
    "dizzy":     "@",
    "dead":      "─",
    "sleepy":    "z",
    "squint":    "·",
    "surprised": "!",
}

_OLED_COL: dict[str, RGB] = {
    "happy":     (255, 150, 175),
    "alert":     (255, 196, 70),
    "hurt":      (255, 85, 75),
    "dizzy":     (255, 85, 75),
    "surprised": (255, 235, 150),
    "sleepy":    (70, 120, 180),
}

# Wheel spin chars: a four-frame rolling animation
WHEEL_CHARS = "◔◑◕◐"


@dataclass
class RoverPose:
    head_cx: int
    head_row: int
    body_top_px: int
    body_bottom_px: int
    left: int
    hubs: list[tuple[int, int]]


# ── Main draw call ──────────────────────────────────────────────────────────

def draw_rover(
    c: Canvas,
    left: int,
    surface: list[int],
    t: float,
    *,
    spin: int,
    mood: str,
    gaze: tuple[int, int],
    dead: bool,
    led: RGB,
    tint: RGB | None,
    alert_flash: bool,
    stuck: bool,
    arm_job: tuple[str, float] | None,
    sample_floor_px: int | None = None,
) -> RoverPose:
    # rover sits on the terrain at its centre column
    cx = left + S.ROVER_W // 2
    ground = surface[min(max(cx, 0), c.w - 1)]
    top = ground - 6   # row 0 (head top) … row 6 (wheels)

    # ── colour modifier ────────────────────────────────────────────────────
    def col(c0: RGB) -> RGB:
        if dead:
            g = int(c0[0] * 0.30 + c0[1] * 0.59 + c0[2] * 0.11)
            g = max(0, int(g * 0.68))
            return (g, g, g + 2)
        if alert_flash:
            return lerp(c0, (165, 38, 38), 0.45)
        return lerp(c0, tint, 0.22) if tint else c0

    # ── helper ──────────────────────────────────────────────────────────────
    def put(rx: int, ry: int, ch: str, fg: RGB, bg: RGB | None = None) -> None:
        c.put(left + rx, top + ry, ch, fg, bg)

    # ── HEAD background fill (rows 0-2, cols 3-10) ──────────────────────────
    hp = col(HEAD_PLATE)
    hb = col(HEAD_BORDER)
    for ry in range(3):
        for rx in range(3, 11):
            put(rx, ry, " ", hb, hp)

    # head outline
    put(3, 0, "┌", hb, hp);  put(10, 0, "┐", hb, hp)
    for rx in range(4, 10):
        put(rx, 0, "─", hb, hp)
    put(3, 1, "│", hb, hp);  put(10, 1, "│", hb, hp)
    put(3, 2, "│", hb, hp);  put(10, 2, "│", hb, hp)

    # OLED icon (row 1, col 6)
    if dead:
        oled_ch, oled_col = "─", col((110, 44, 40))
    else:
        raw = _OLED_ICON.get(mood, "·")
        oled_ch = ".·●"[int(t * 4) % 3] if raw is None else raw
        oled_col = col(_OLED_COL.get(mood, (110, 225, 255)))
    put(6, 1, oled_ch, oled_col, hp)

    # eyes (row 2, cols 5 and 8)
    if dead:
        eye_ch = "×"
        eye_fg = col((96, 68, 65))
    else:
        eye_ch = _EYE.get(mood, "◉")
        eye_fg = col(_EYE_COL.get(mood, (110, 220, 255)))
    put(5, 2, eye_ch, eye_fg, hp)
    put(8, 2, eye_ch, eye_fg, hp)

    # ── CHASSIS background fill (rows 3-5, all 14 cols) ────────────────────
    ch  = col(CHASSIS)
    chi = col(CHASSIS_HI)
    for ry in range(3, 6):
        for rx in range(14):
            put(rx, ry, " ", chi, ch)

    # chassis top (row 3): joins head at cols 3 and 10
    put(0,  3, "╔", chi, ch);  put(13, 3, "╗", chi, ch)
    put(1,  3, "═", chi, ch);  put(2,  3, "═", chi, ch)
    put(3,  3, "╧", hb,  ch)   # head join left
    for rx in range(4, 10):
        put(rx, 3, "═", chi, ch)
    put(10, 3, "╧", hb,  ch)   # head join right
    put(11, 3, "═", chi, ch);  put(12, 3, "═", chi, ch)

    # chassis body (row 4): name + status LED
    put(0, 4, "║", chi, ch);  put(13, 4, "║", chi, ch)
    name = S.ROVER_NAME[:4].upper()
    nx = (14 - len(name)) // 2   # = 5 for "ROVY"
    name_col = col(NAME_GOLD) if not dead else col(CHASSIS_HI)
    for i, char in enumerate(name):
        put(nx + i, 4, char, name_col, ch)
    if not dead:
        pulse = 0.68 + 0.32 * math.sin(t * 5)
        lc = led or LED_COLORS.get(mood, (70, 235, 110))
        put(11, 4, "·", lerp(col(lc), (255, 255, 255), 0.42 * pulse), ch)

    # chassis bottom (row 5): three strut points
    st = col(STRUT)
    put(0,  5, "╚", chi, ch);  put(13, 5, "╝", chi, ch)
    put(1,  5, "═", chi, ch);  put(2,  5, "═", chi, ch)
    put(3,  5, "╤", st,  ch)
    put(4,  5, "═", chi, ch);  put(5,  5, "═", chi, ch);  put(6, 5, "═", chi, ch)
    put(7,  5, "╤", st,  ch)
    put(8,  5, "═", chi, ch);  put(9,  5, "═", chi, ch);  put(10, 5, "═", chi, ch)
    put(11, 5, "╤", st,  ch)
    put(12, 5, "═", chi, ch)

    # ── sampling arm indicator ──────────────────────────────────────────────
    if arm_job is not None:
        instrument, phase = arm_job
        tip_col = {
            "camera": (255, 255, 255),
            "spectrometer": (255, 90, 80),
            "drill": (255, 214, 120),
        }.get(instrument, (255, 255, 255))
        put(12, 5, "↓", col(tip_col), ch)
        if sample_floor_px is not None:
            arm_abs_x = left + 12
            for y in range(top + 6, sample_floor_px // 2 + 1):
                c.put(arm_abs_x, y, "│", col(STRUT))

    # ── wheels (row 6) ──────────────────────────────────────────────────────
    wch = WHEEL_CHARS[spin % 4]
    wfg = col(WHEEL_HI)
    wbg = col(WHEEL)
    for rx in S.WHEEL_X:
        # each wheel sits at its own terrain height for natural grounding
        wr = surface[min(max(left + rx, 0), c.w - 1)]
        c.put(left + rx, wr, wch, wfg, wbg)

    # ── pose ────────────────────────────────────────────────────────────────
    hubs = [(left + rx, surface[min(max(left + rx, 0), c.w - 1)] * 2 + 1) for rx in S.WHEEL_X]
    return RoverPose(
        head_cx=left + 7,
        head_row=top,
        body_top_px=top * 2,
        body_bottom_px=(top + 5) * 2 + 1,
        left=left,
        hubs=hubs,
    )
