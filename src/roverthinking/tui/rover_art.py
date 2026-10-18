"""Draws Rovy as soft pixel art with a character-glyph face.

The body — antennae, rounded head bezel, beveled chassis, treaded wheels — is painted
into the canvas pixel layer (half-blocks at 2x vertical resolution) so it gets rounded
corners and shading. The parts that must stay crisp and legible — the two eyes, the
mouth/OLED indicator, the gold "ROVY" nameplate and the status LED — are drawn as
character glyphs *on top* of the flushed pixels, so Rovy keeps her eleven moods.

Footprint is unchanged: 14 columns wide x 7 text rows tall, head top at `top`. The
antennae poke one text row above the head. Grid (text rows; px = pixel rows = 2x):

  row -1  │     ╷     ╷     │  antennae (glowing tips)
  row 0   │    ╭───────╮    │  head bezel top (rounded)
  row 1   │    │ ◉   ◉ │    │  eyes (expression + colour change with mood)
  row 2   │    │   ·   │    │  mouth / OLED indicator
  row 3   │  ▟█████████▙  │    chassis top bevel
  row 4   │  █[ ROVY ]·█  │    recessed nameplate + status LED
  row 5   │  ▜██╤═══╤═══╤▛  │  chassis bottom bevel + struts
  row 6   │    ◐   ◐   ◐    │  wheels (spin animated, blue rim + hub)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from roverthinking.tui import sprites as S
from roverthinking.tui.canvas import RGB, Canvas, lerp

# ── Palette ────────────────────────────────────────────────────────────────

BEZEL_HI:    RGB = (182, 190, 210)   # lit edge of the head shell
BEZEL_LO:    RGB = (92, 98, 118)     # shadowed edge of the head shell
SCREEN:      RGB = (16, 18, 26)      # dark face "glass"
SCREEN_GLOW: RGB = (26, 40, 56)      # faint backlight inside the glass
CHASSIS_HI:  RGB = (58, 64, 84)      # top bevel of the body
CHASSIS_MID: RGB = (30, 34, 46)      # body midtone
CHASSIS_LO:  RGB = (12, 13, 20)      # bottom bevel of the body
PLATE_INSET: RGB = (42, 46, 62)      # recessed nameplate panel
NAME_GOLD:   RGB = (224, 180, 70)
STRUT:       RGB = (120, 126, 142)
WHEEL:       RGB = (24, 26, 38)
WHEEL_RIM:   RGB = (92, 150, 205)    # blue-ish tyre rim
ANT_STALK:   RGB = (120, 128, 146)

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
    "blink":     "–",
    "happy":     "◠",
    "alert":     "◉",
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
    "normal":    (120, 225, 255),
    "blink":     (70, 155, 188),
    "happy":     (135, 255, 175),
    "alert":     (255, 200, 80),
    "thinking":  (130, 205, 255),
    "hurt":      (255, 90, 80),
    "dizzy":     (255, 90, 80),
    "dead":      (96, 88, 86),
    "sleepy":    (70, 120, 180),
    "squint":    (100, 185, 220),
    "surprised": (255, 235, 150),
}

# OLED/mouth single-char icon per mood  (None = animated)
_OLED_ICON: dict[str, str | None] = {
    "normal":    "‿",
    "blink":     "‿",
    "happy":     "◡",
    "alert":     "▲",
    "thinking":  None,   # cycles . o O
    "hurt":      "□",
    "dizzy":     "~",
    "dead":      "–",
    "sleepy":    "z",
    "squint":    "·",
    "surprised": "o",
}

_OLED_COL: dict[str, RGB] = {
    "happy":     (255, 150, 175),
    "alert":     (255, 196, 70),
    "hurt":      (255, 85, 75),
    "dizzy":     (255, 85, 75),
    "surprised": (255, 235, 150),
    "sleepy":    (70, 120, 180),
}

# Wheel spin: a four-frame rolling animation (rim rotating around the hub)
WHEEL_CHARS = "◐◓◑◒"


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
    top = ground - 6              # text row 0 (head top) … row 6 (wheels)
    pbr = top * 2                 # pixel-row origin (pixel layer is 2x vertical)

    # ── colour modifier (dead / alert flash / safe-mode tint) ───────────────
    def col(c0: RGB) -> RGB:
        if dead:
            g = int(c0[0] * 0.30 + c0[1] * 0.59 + c0[2] * 0.11)
            g = max(0, int(g * 0.68))
            return (g, g, g + 2)
        if alert_flash:
            return lerp(c0, (165, 38, 38), 0.45)
        return lerp(c0, tint, 0.22) if tint else c0

    # pixel into the rover's local frame: rx = column 0..13, pr = pixel row (0 = head top)
    def px(rx: int, pr: int, c0: RGB) -> None:
        c.px(left + rx, pbr + pr, col(c0))

    # character overlay in the rover's local frame (text rows)
    def put(rx: int, ry: int, ch: str, fg: RGB, bg: RGB | None = None) -> None:
        c.put(left + rx, top + ry, ch, col(fg), col(bg) if bg is not None else None)

    # ── ANTENNAE (one text row above the head) ───────────────────────────────
    blink = 0.6 + 0.4 * math.sin(t * 4)
    tip = led or _EYE_COL.get(mood, (120, 225, 255))
    for ax in (4, 9):
        px(ax, -2, lerp(col(ANT_STALK), col(tip), 0.2 + 0.6 * blink) if not dead else col(ANT_STALK))
        px(ax, -1, col(ANT_STALK))

    # ── HEAD shell (pixel rows 0..5, cols 3..10) with rounded corners ───────
    # interior face glass, with a faint top-down backlight
    for pr in range(1, 5):
        for rx in range(4, 10):
            px(rx, pr, lerp(SCREEN_GLOW, SCREEN, pr / 5))
    # top + bottom bezel (corners left empty → rounded)
    for rx in range(4, 10):
        px(rx, 0, BEZEL_HI)
        px(rx, 5, BEZEL_LO)
    # left (lit) + right (shadowed) sides
    for pr in range(1, 5):
        px(3, pr, BEZEL_HI)
        px(10, pr, BEZEL_LO)

    # ── CHASSIS (pixel rows 6..11, cols 0..13), beveled + rounded ───────────
    for pr in range(7, 11):                       # body midtone, gently graded
        f = (pr - 7) / 4
        for rx in range(0, 14):
            px(rx, pr, lerp(CHASSIS_MID, CHASSIS_LO, 0.35 * f))
    for rx in range(1, 13):                        # lit top bevel (row 6), rounded ends
        px(rx, 6, CHASSIS_HI)
    for rx in range(1, 13):                        # dark bottom bevel (row 11), rounded ends
        px(rx, 11, CHASSIS_LO)
    px(0, 7, CHASSIS_LO); px(13, 7, CHASSIS_LO)    # soften the top corners
    # recessed nameplate panel behind "ROVY" (text row 4 → pixel rows 8,9)
    for pr in (8, 9):
        for rx in range(4, 10):
            px(rx, pr, PLATE_INSET)

    # ── STRUTS down to the wheels (pixel row 12) ────────────────────────────
    for rx in S.WHEEL_X:
        px(rx, 12, STRUT)

    # commit the body to the cell grid before drawing the crisp glyph face
    c.flush_pixels()

    # ── FACE glyphs (drawn on top of the flushed pixels) ─────────────────────
    gx = max(-1, min(1, gaze[0]))
    face_bg = SCREEN if not dead else (18, 18, 20)
    # eyes (text row 1, cols 5 and 8), nudged by gaze
    eye_ch = _EYE.get(mood, "◉") if not dead else "×"
    eye_fg = _EYE_COL.get(mood, (120, 225, 255)) if not dead else (96, 68, 65)
    glow = lerp(face_bg, eye_fg, 0.0 if dead else 0.22)
    put(5 + gx, 1, eye_ch, eye_fg, glow)
    put(8 + gx, 1, eye_ch, eye_fg, glow)
    # mouth / OLED indicator (text row 2, col 6)
    if dead:
        mouth_ch, mouth_col = "–", (110, 44, 40)
    else:
        raw = _OLED_ICON.get(mood, "‿")
        mouth_ch = ".oO"[int(t * 4) % 3] if raw is None else raw
        mouth_col = _OLED_COL.get(mood, (110, 225, 255))
    put(6, 2, mouth_ch, mouth_col, face_bg)

    # ── NAMEPLATE + status LED (text row 4) ─────────────────────────────────
    name = S.ROVER_NAME[:4].upper()
    nx = (14 - len(name)) // 2    # = 5 for "ROVY"
    name_col = NAME_GOLD if not dead else CHASSIS_HI
    for i, char in enumerate(name):
        put(nx + i, 4, char, name_col, PLATE_INSET)
    if not dead:
        pulse = 0.68 + 0.32 * math.sin(t * 5)
        lc = led or LED_COLORS.get(mood, (70, 235, 110))
        put(11, 4, "●", lerp(lc, (255, 255, 255), 0.42 * pulse), CHASSIS_MID)

    # ── sampling arm indicator ──────────────────────────────────────────────
    if arm_job is not None:
        instrument, _ = arm_job
        tip_col = {
            "camera": (255, 255, 255),
            "spectrometer": (255, 90, 80),
            "drill": (255, 214, 120),
        }.get(instrument, (255, 255, 255))
        put(12, 5, "↓", tip_col, CHASSIS_MID)
        if sample_floor_px is not None:
            arm_abs_x = left + 12
            for y in range(top + 6, sample_floor_px // 2 + 1):
                c.put(arm_abs_x, y, "│", col(STRUT))

    # ── WHEELS (text row 6) — each sits on its own terrain height ────────────
    # (drawn as glyphs *after* the pixel flush, so the scene's later flush leaves them alone)
    wch = WHEEL_CHARS[spin % 4]
    for rx in S.WHEEL_X:
        wr = surface[min(max(left + rx, 0), c.w - 1)]
        c.put(left + rx, wr, wch, col(WHEEL_RIM), col(WHEEL))

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
