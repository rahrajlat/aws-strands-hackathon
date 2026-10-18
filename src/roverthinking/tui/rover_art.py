"""Draws Rovy, the rover, as shaded pixel art with an expressive face. No Textual imports.

Pixels are painted into the canvas's pixel layer (two per text cell) and composited with
half-block characters by `Canvas.flush_pixels`. Each wheel is placed on the terrain directly
beneath it and struts reach up to the chassis, so the rocker-bogie suspension visibly flexes.
The face is two silver sensor-ring eyes whose pupils look around and whose eyelids show
emotion, plus a small OLED that shows an icon for her mood.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from roverthinking.tui import sprites as S
from roverthinking.tui.canvas import RGB, Canvas, lerp

BLACK: RGB = (22, 23, 28)
BLACK_HI: RGB = (52, 54, 66)
BLACK_LO: RGB = (13, 13, 17)
PLATE: RGB = (16, 17, 21)  # the face plate: also the colour of the eyelids
SILVER: RGB = (205, 210, 222)
SILVER_LO: RGB = (140, 146, 160)
MESH: RGB = (52, 57, 68)
PUPIL: RGB = (14, 15, 20)
GOLD: RGB = (226, 178, 70)
RED_PCB: RGB = (150, 34, 54)
RED_PCB_HI: RGB = (188, 58, 78)
STEEL: RGB = (170, 174, 188)
TIRE: RGB = (30, 30, 36)
TIRE2: RGB = (56, 56, 66)
HUB: RGB = (196, 200, 212)
OLED_BG: RGB = (8, 18, 44)
OLED_FG: RGB = (110, 225, 255)

LED_COLORS = {
    "normal": (70, 255, 100),
    "blink": (70, 255, 100),
    "thinking": (90, 220, 255),
    "squint": (90, 220, 255),
    "happy": (120, 255, 150),
    "alert": (255, 196, 70),
    "surprised": (255, 226, 140),
    "hurt": (255, 80, 70),
    "dizzy": (255, 80, 70),
    "dead": (60, 70, 62),
    "sleepy": (70, 120, 180),
}
RING_TINT = {
    "alert": (255, 196, 80),
    "surprised": (255, 226, 150),
    "hurt": (255, 110, 100),
    "dizzy": (255, 110, 100),
    "happy": (140, 255, 170),
    "thinking": (130, 225, 255),
}
FONT = {  # a tiny 3x4 pixel font, just enough for the gold logo
    "R": ["##.", "#.#", "##.", "#.#"],
    "O": ["###", "#.#", "#.#", "###"],
    "V": ["#.#", "#.#", "#.#", ".#."],
    "Y": ["#.#", ".#.", ".#.", ".#."],
}


@dataclass
class RoverPose:
    """Where the parts ended up, so the scene can attach speech bubbles, the shield and so on."""

    head_cx: int  # cell column of the head's centre
    head_row: int  # cell row of the top of the head
    body_top_px: int
    body_bottom_px: int
    left: int
    hubs: list[tuple[int, int]]


def _line(c: Canvas, x0: int, y0: int, x1: int, y1: int, color: RGB) -> None:
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        c.px(x0, y0, color)
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _eye(P, ex: int, ey: int, mood: str, gaze: tuple[int, int], t: float, side: int, dead: bool) -> None:
    """One sensor-ring eye, 5x5 pixels, with a roaming pupil and mood eyelids."""
    ring = RING_TINT.get(mood)
    for dy in range(5):
        for dx in range(5):
            d = math.hypot(dx - 2, dy - 2)
            if d > 2.6:
                continue
            if d > 1.6:
                col = lerp(SILVER, ring, 0.45) if ring else (SILVER if dy < 3 else SILVER_LO)
            else:
                col = MESH if (dx + dy) % 2 == 0 else lerp(MESH, SILVER_LO, 0.25)
            P(ex + dx, ey + dy, col)
    # the pupil: the dark transducer centre, which looks where Rovy is looking
    gx, gy = gaze
    if mood == "dizzy":
        gx, gy = round(math.cos(t * 9 + side)), round(math.sin(t * 9 + side))
    cx, cy = 2 + gx, 2 + gy
    pr = 0.8 if mood == "surprised" else 1.2
    for dy in range(5):
        for dx in range(5):
            if math.hypot(dx - cx, dy - cy) <= pr and math.hypot(dx - 2, dy - 2) <= 2.2:
                P(ex + dx, ey + dy, PUPIL)
    if mood not in ("dead", "dizzy", "blink", "sleepy"):
        P(ex + max(0, cx - 1), ey + max(0, cy - 1), (255, 255, 255))  # catchlight
    if dead or mood == "dizzy":
        col = (255, 90, 80) if not dead else (190, 70, 60)
        for i in (1, 2, 3):
            P(ex + i, ey + i, col)
            P(ex + 4 - i, ey + i, col)
    # eyelids, drawn in the colour of the face plate
    top = bottom = 0
    if mood == "blink":
        top = 5
    elif mood == "sleepy":
        top = 3
    elif mood == "squint":
        top, bottom = 1, 1
    for dy in range(5):
        for dx in range(5):
            if math.hypot(dx - 2, dy - 2) > 2.6:
                continue
            cover = dy < top or dy >= 5 - bottom
            if mood == "happy":  # a cheerful upward arc: the lower lid rises
                cover = dy >= (3 if abs(dx - 2) <= 1 else 2)
            elif mood == "hurt":  # worried: the outer upper corners droop shut
                outer = (dx < 2) if side == 0 else (dx > 2)
                cover = dy < (2 if outer else 0)
            if cover:
                P(ex + dx, ey + dy, PLATE)


_OLED_ICONS = {
    "happy": [".#.#.", "#####", "..#.."],
    "alert": ["#.#", "#.#", "..."],
    "sleepy": ["###", ".#.", "###"],
    "dizzy": ["#.#", ".#.", "#.#"],
    "hurt": ["#.#", ".#.", "#.#"],
    "squint": ["###", "#.#", ".#."],
}


def _oled(c: Canvas, P, mood: str, t: float, dead: bool) -> None:
    x0, y0, x1, y1 = S.OLED
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            P(x, y, OLED_BG)
    w = x1 - x0 + 1
    if dead:
        for x in range(x0 + 1, x1):
            P(x, y0 + 1, (120, 50, 46))
        return
    if mood in ("normal", "blink"):  # a gently moving heartbeat line
        for i in range(w):
            P(x0 + i, y0 + 1 + round(math.sin(t * 3.2 + i * 0.9)), OLED_FG)
    elif mood == "thinking":
        for i, dx in enumerate((2, 4, 6)):
            P(x0 + dx, y1, OLED_FG if int(t * 4) % 3 == i else (40, 80, 110))
    elif mood in _OLED_ICONS:
        icon = _OLED_ICONS[mood]
        ox = x0 + (w - max(len(r) for r in icon)) // 2
        col = (
            (255, 196, 80) if mood == "alert" else ((255, 100, 90) if mood in ("hurt", "dizzy") else OLED_FG)
        )
        for j, row in enumerate(icon):
            for i, ch in enumerate(row):
                if ch == "#":
                    P(ox + i, y0 + j, col)


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
    def col(c0: RGB) -> RGB:
        if dead:
            g = int((c0[0] * 0.3 + c0[1] * 0.59 + c0[2] * 0.11) * 0.7)
            return (g, g, g + 2)
        if alert_flash and c0 in (BLACK, BLACK_HI):
            return (120, 40, 40)
        return lerp(c0, tint, 0.22) if tint else c0

    # --- where each wheel sits: on the terrain directly beneath it
    sink = 2 if stuck else 0
    hubs: list[tuple[int, int]] = []
    tops = []
    for rx in S.WHEEL_X:
        x = left + rx
        s = surface[min(max(x, 0), c.w - 1)]
        top = 2 * s - S.WHEEL_PX + sink
        tops.append(top)
        hubs.append((x, top + S.WHEEL_PX // 2))
    body_bottom = min(tops) - S.STRUT_PX - 1
    top0 = body_bottom - (S.BODY_PX - 1)

    def P(rx: int, ry: int, color: RGB) -> None:
        c.px(left + rx, top0 + ry, color)

    def R(x0: int, y0: int, x1: int, y1: int, color: RGB) -> None:
        c.rect(left + x0, top0 + y0, left + x1, top0 + y1, color)

    # --- suspension first (it sits behind the wheels): struts and the rocker-bogie bar
    for (hx, hy), ax in zip(hubs, S.STRUT_X, strict=True):
        _line(c, left + ax, body_bottom + 1, hx, hy, col(BLACK_HI))
    (rx0, ry0), (rx1, ry1) = hubs[0], hubs[1]
    _line(c, rx0, ry0, rx1, ry1, col(STEEL))

    # --- red circuit board on the back, with a whip antenna
    x0, y0, x1, y1 = S.PCB
    R(x0, y0, x1, y1, col(RED_PCB))
    R(x0, y0, x1, y0, col(RED_PCB_HI))
    for hx in range(x0 + 1, x1, 2):
        P(hx, y0, col(GOLD))  # header pins
    R(9, 9, 10, 9, col(SILVER_LO))  # a chip
    R(13, 9, 15, 9, col(MESH))
    ax, ay = S.ANTENNA
    R(ax, ay, ax, y0 - 1, col(STEEL))
    P(ax, ay, col(SILVER))
    if not dead:
        glow = 0.5 + 0.5 * math.sin(t * 4)
        P(ax, ay - 1, lerp((90, 40, 40), (255, 90, 80), glow))  # a little blinking beacon

    # --- the black chassis, with the gold logo
    cx0, cy0, cx1, cy1 = S.CHASSIS
    R(cx0, cy0, cx1, cy1, col(BLACK))
    R(cx0, cy0, cx1, cy0, col(BLACK_HI))
    R(cx0, cy1, cx1, cy1, col(BLACK_LO))
    R(cx0, cy0, cx0, cy1, col(BLACK_LO))
    for i, ch in enumerate(S.ROVER_NAME.upper()[:4]):
        for j, row in enumerate(FONT.get(ch, FONT["O"])):
            for k, px in enumerate(row):
                if px == "#":
                    P(7 + i * 4 + k, 12 + j, col(GOLD) if not dead else col(BLACK_HI))

    # --- two glowing lights under the face, with a soft halo
    lc = LED_COLORS.get(mood, LED_COLORS["normal"]) if led is None else led
    pulse = 0.82 + 0.18 * math.sin(t * 5)
    for lx, ly in S.LEDS:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                d = math.hypot(dx, dy)
                if d <= 1.3:
                    P(
                        lx + dx,
                        ly + dy,
                        lerp(lc, (255, 255, 255), 0.35) if d < 0.8 else lerp(BLACK, lc, pulse),
                    )
                elif d <= 2.1 and not dead and ly + dy > cy0 and ly + dy < cy1:
                    P(lx + dx, ly + dy, lerp(BLACK, lc, 0.28 * pulse))

    # --- the head: a face plate with two sensor-ring eyes and an OLED above them
    hx0, hy0, hx1, hy1 = S.HEAD
    R(hx0, hy0, hx1, hy1, col(BLACK))
    R(hx0, hy0, hx1, hy0, col(BLACK_HI))
    R(hx0 + 1, hy0 + 1, hx1 - 1, hy1 - 1, col(PLATE))
    R(hx0 - 1, hy0 + 4, hx0 - 1, hy1 - 1, col(BLACK_LO))  # side bumps
    R(hx1 + 1, hy0 + 4, hx1 + 1, hy1 - 1, col(BLACK_LO))
    _oled(c, P, mood, t, dead)
    for side, ex in enumerate(S.EYE_X):
        _eye(P, ex, S.EYE_Y, mood, gaze, t, side, dead)

    # --- the sampling arm: folded at the front, or reaching the ground when sampling
    ax2, ay2 = S.ARM
    R(ax2, ay2, ax2 + 2, ay2, col(STEEL))
    R(ax2 + 2, ay2 - 1, ax2 + 3, ay2 + 1, col(BLACK_HI))
    if arm_job is not None:
        instrument, phase = arm_job
        tipc = {"camera": (255, 255, 255), "spectrometer": (255, 90, 80), "drill": (255, 214, 120)}.get(
            instrument, (255, 255, 255)
        )
        floor = sample_floor_px if sample_floor_px is not None else top0 + 26
        tx = left + ax2 + 3
        for y in range(top0 + ay2 + 2, floor):
            c.px(tx, y, col(STEEL))
        bob = int(phase * 8) % 2 if instrument == "drill" else 0
        c.px(tx, floor - bob, tipc)
        if instrument == "spectrometer" and int(phase * 6) % 2:
            c.px(tx, floor + 1, (255, 90, 80))

    # --- wheels: ridged black tyres with silver hub caps that turn with the rover's travel
    r = S.WHEEL_PX // 2
    for hx, hy in hubs:
        for dy in range(-r, r):
            for dx in range(-r, r):
                d = math.hypot(dx + 0.5, dy + 0.5)
                if d > r:
                    continue
                ang = math.atan2(dy + 0.5, dx + 0.5)
                if d > r - 1.0:
                    tooth = int((ang + math.pi) / (2 * math.pi) * 8 + spin * 0.7) % 2
                    colr = TIRE2 if tooth else TIRE
                elif d > 1.0:
                    colr = (
                        lerp(HUB, TIRE, 0.4) if int((ang + math.pi) / (math.pi / 2) + spin * 0.6) % 2 else HUB
                    )
                else:
                    colr = HUB
                c.px(hx + dx, hy + dy, col(colr))

    return RoverPose(
        head_cx=left + (hx0 + hx1) // 2,
        head_row=top0 // 2,
        body_top_px=top0,
        body_bottom_px=body_bottom,
        left=left,
        hubs=hubs,
    )
