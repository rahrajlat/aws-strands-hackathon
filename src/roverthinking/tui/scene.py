"""Draws one frame of the traverse: sky, mountains, ground, hazards, the rover, effects.

Pure Python (no Textual), so a frame can be rendered and inspected in tests. The scene only
reads the rover's state; the simulation never knows it exists.
"""

from __future__ import annotations

import math
import random

from roverthinking.sim.rover import GOAL_M, SHIELD_DEPLOY, SHIELD_RETRACT, Hazard, Rover
from roverthinking.tui import rover_art, sprites, terrain
from roverthinking.tui.canvas import RGB, Canvas, lerp
from roverthinking.tui.effects import EffectsLayer
from roverthinking.tui.terrain import METRES_PER_CELL as MPC

DAY_TOP, DAY_HOR = (84, 64, 120), (226, 150, 98)
NIGHT_TOP, NIGHT_HOR = (5, 5, 18), (46, 26, 42)
DUST_TOP, DUST_HOR = (130, 78, 46), (176, 112, 66)
GROUND_TOP, GROUND_DEEP = (150, 82, 52), (70, 34, 28)
SEV_COLOR = {1: (255, 210, 90), 2: (255, 150, 60), 3: (255, 70, 60)}
ROCK_COL: RGB = (150, 125, 110)
LABELS = {
    "dust_storm": "DUST STORM",
    "rockfall": "ROCKFALL",
    "sand_trap": "SAND TRAP",
    "steep_slope": "STEEP SLOPE",
    "sensor_fault": "SENSOR FAULT",
    "solar_flare": "SOLAR FLARE",
    "meteor_shower": "METEOR SHOWER",
    "strange_rock": "STRANGE ROCK",
}
LED_COLOR = {
    "driving": (110, 235, 130),
    "held": (255, 190, 80),
    "safe": (110, 210, 255),
    "shielded": (90, 210, 255),
    "stuck": (255, 80, 60),
    "working": (110, 210, 255),
    "dead": (90, 90, 95),
    "done": (110, 235, 130),
}


def shade(c: RGB, f: float) -> RGB:
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f))


class SceneRenderer:
    def __init__(self) -> None:
        r = random.Random(11)
        self.stars = [(r.random(), r.random() * 0.8, r.choice(".+*.")) for _ in range(80)]
        self.dust = 0.0

    def render(self, rover: Rover, w: int, h: int, tick_frac: float, t: float, fx: EffectsLayer) -> Canvas:
        gy = max(8, int(h * 0.70))
        cx = max(int(w * 0.32), sprites.ROVER_W // 2 + 3)
        cam = rover.x + rover.velocity * tick_frac
        target = 1.0 - rover.visibility if rover.visibility < 1.0 else 0.0
        self.dust += (target - self.dust) * 0.06
        day = rover.daylight
        light = 0.32 + 0.68 * day
        c = Canvas(w, h)
        self._sky(c, gy, day, light)
        self._stars(c, gy, day, t)
        self._sun(c, rover, gy, w)
        self._mountains(c, cam, gy, light)
        surface = self._ground(c, rover, cam, cx, gy, h, light)
        self._props(c, rover, cam, cx, surface, light, t)
        ground_row = surface[min(max(cx, 0), w - 1)]
        pose = self._rover(c, rover, cx, surface, t, fx)
        self._markers(c, rover, cam, cx, surface, gy, t)
        self._effects(c, fx, cam, cx, surface, gy, ground_row)
        self._bubble(c, fx, pose)
        self._fog(c, cx)
        c.tint(*fx.flash)
        return c

    # ---- backdrop -----------------------------------------------------------------------------

    def _sky(self, c: Canvas, gy: int, day: float, light: float) -> None:
        for y in range(gy + 1):
            f = y / max(gy, 1)
            clear = lerp(lerp(NIGHT_TOP, NIGHT_HOR, f), lerp(DAY_TOP, DAY_HOR, f), day**0.6)
            dusty = shade(lerp(DUST_TOP, DUST_HOR, f), 0.35 + 0.65 * day)
            col = lerp(clear, dusty, min(1.0, self.dust * 0.9))
            for x in range(c.w):
                c.bg[y][x] = col

    def _stars(self, c: Canvas, gy: int, day: float, t: float) -> None:
        vis = (1 - day) * (1 - self.dust)
        if vis < 0.15:
            return
        for i, (fx_, fy, ch) in enumerate(self.stars):
            tw = 0.6 + 0.4 * math.sin(t * 2 + i)
            x, y = int(fx_ * c.w), int(fy * gy * 0.85)
            c.put(x, y, ch, shade((235, 225, 255), vis * tw))
        c.put(int(c.w * 0.07), 3, "o", shade((120, 180, 255), vis + 0.2))

    def _sun(self, c: Canvas, rover: Rover, gy: int, w: int) -> None:
        hr = rover.hour
        if not 6 <= hr <= 18:
            return
        u = (hr - 6) / 12
        sx, sy = int(w * (0.08 + 0.84 * u)), int(gy * (0.9 - 0.72 * math.sin(math.pi * u)))
        col = lerp((255, 230, 170), (200, 150, 110), self.dust)
        fade = 1 - self.dust * 0.8
        for dx, dy, ch in ((0, 0, "O"), (-2, 0, "-"), (2, 0, "-"), (0, -1, "|"), (0, 1, "|")):
            c.put(sx + dx, sy + dy, ch, shade(col, fade))

    def _mountains(self, c: Canvas, cam: float, gy: int, light: float) -> None:
        for factor, base, amp, col in ((0.08, 4, 3, (96, 60, 84)), (0.22, 2, 3, (112, 66, 62))):
            shaded = shade(lerp(col, (150, 95, 60), self.dust * 0.6), light)
            for x in range(c.w):
                u = x + cam / MPC * factor
                hgt = int(base + amp * (0.5 + 0.5 * math.sin(u / 9.0)) + 1.5 * math.sin(u / 3.7))
                for y in range(max(0, gy - hgt), gy + 1):
                    c.bg[y][x] = shaded

    def _ground(self, c, rover, cam, cx, gy, h, light) -> list[int]:
        surface = []
        sand = [hz for hz in rover.hazards if hz.kind == "sand_trap" and hz.x is not None]
        for x in range(c.w):
            wx = cam + (x - cx) * MPC
            sr = gy - terrain.offset(wx, rover)
            sr = min(max(sr, 2), h - 2)
            surface.append(sr)
            for y in range(gy + 1, sr):
                c.bg[y][x] = c.bg[gy][x]
            in_sand = any(hz.x <= wx <= hz.x + hz.length for hz in sand)
            for y in range(sr, h):
                depth = (y - sr) / max(h - sr, 1)
                col = shade(lerp(GROUND_TOP, GROUND_DEEP, min(1.0, depth * 1.3)), light)
                if in_sand and y - sr < 2:
                    col = shade((205, 165, 105), light)
                if y == sr:
                    col = lerp(col, (255, 200, 150), 0.18 * light)
                c.bg[y][x] = col
                n = terrain.hash01(int(wx / MPC) + 7919 * y, y)
                if n < 0.05 and y > sr:
                    c.put(x, y, ".,:"[int(n * 100) % 3], lerp(col, (255, 210, 170), 0.22))
            if in_sand:
                c.put(x, sr, "~", shade((235, 205, 140), light), c.bg[sr][x])
            if 0 < wx < rover.x - 14 and c.ch[sr + 1][x] == " " and sr + 2 < h:
                cell = int(wx / MPC)
                c.put(x, sr + 1, ":" if cell % 2 == 0 else ".", shade((96, 52, 38), light + 0.2))
        return surface

    # ---- things on the ground -------------------------------------------------------------------

    def _col(self, wx: float, cam: float, cx: int) -> int:
        return round(cx + (wx - cam) / MPC)

    def _props(self, c, rover, cam, cx, surface, light, t) -> None:
        for hz in rover.hazards:
            if hz.x is None:
                continue
            if hz.kind == "rockfall":
                self._boulders(c, rover, hz, cam, cx, surface, light)
            elif hz.kind == "strange_rock":
                self._rock(c, rover, hz, cam, cx, surface, t)
            elif hz.kind == "steep_slope":
                for x in range(c.w):
                    wx = cam + (x - cx) * MPC
                    if hz.x <= wx <= hz.x + hz.length and terrain.hash01(x, 3) < 0.35:
                        c.put(x, surface[x], "^", shade((190, 120, 85), light))
            if hz.bypass and not hz.resolved:
                for x in range(c.w):
                    wx = cam + (x - cx) * MPC
                    if hz.x - 10 <= wx <= hz.x + hz.length + 10 and x % 2 == 0:
                        c.put(x, surface[x] - 2, ".", (120, 235, 150))
        gx = self._col(GOAL_M, cam, cx)
        if -20 < gx < c.w + 20:
            base = surface[min(max(gx, 0), c.w - 1)]
            col = shade((210, 215, 230), 0.5 + 0.5 * light)
            c.sprite(gx - 3, base - len(sprites.STATION), sprites.STATION, col)
            c.text(gx - 4, base - len(sprites.STATION) - 1, "SCIENCE SITE", (120, 235, 150))

    def _boulders(self, c, rover, hz: Hazard, cam, cx, surface, light) -> None:
        rng = random.Random(hz.id * 977)
        n = int(hz.length) + 4 + 2 * hz.severity
        for i in range(n):
            wx = hz.x + rng.uniform(0, hz.length)
            ch = rng.choice("^^o")
            tier = rng.random() < 0.3
            if rover.tick_n - hz.start_tick < 8 + (i % 6):
                continue
            x = self._col(wx, cam, cx)
            if 0 <= x < c.w:
                col = shade(ROCK_COL, 0.55 + 0.45 * light)
                c.put(x, surface[x] - 1, ch, col)
                if tier:
                    c.put(x, surface[x] - 2, "o", col)

    def _rock(self, c, rover, hz: Hazard, cam, cx, surface, t) -> None:
        x = self._col(hz.x, cam, cx)
        if not 0 <= x < c.w:
            return
        if hz.resolved:
            c.put(x, surface[x] - 1, "o", (110, 90, 85))
            return
        pulse = 0.6 + 0.4 * math.sin(t * 5)
        c.put(x, surface[x] - 1, "#", shade((255, 120, 230), pulse))
        c.put(x - 1, surface[x] - 1, "#", shade((200, 90, 190), pulse))
        if int(t * 4) % 2:
            c.put(x + 1, surface[x] - 2, "*", (255, 245, 170))
        else:
            c.put(x - 2, surface[x] - 2, "+", (255, 245, 170))

    # ---- the rover ----------------------------------------------------------------------------------

    def _mood(self, rover: Rover, fx: EffectsLayer, t: float) -> str:
        """Rovy's expression, from what is happening to her right now."""
        if rover.mode == "dead":
            return "dead"
        if rover.mode == "done" or fx.joy > 0:
            return "happy"
        if fx.hurt > 0:
            return "hurt"
        if rover.stuck:
            return "dizzy"
        if rover.shield == "deploying":
            return "surprised"
        if rover.shield in ("up", "retracting") or rover.job is not None:
            return "squint"
        if rover.safe:
            return "sleepy"
        if fx.pending:
            return "thinking"
        if any(h.response is None for h in rover.detected_hazards()):
            return "alert"
        return "blink" if (t % 4.3) < 0.14 else "normal"

    def _gaze(self, rover: Rover, mood: str, t: float) -> tuple[int, int]:
        if mood == "thinking":
            return (-1, -1)
        ahead = [h for h in rover.detected_hazards() if h.x is not None and h.x > rover.x]
        if ahead or mood in ("alert", "surprised"):
            return (1, 0)
        return ([0, 0, 1, 0, -1, 0][int(t * 0.7) % 6], 0)

    def _rover(
        self, c: Canvas, rover: Rover, cx: int, surface: list[int], t: float, fx: EffectsLayer
    ) -> rover_art.RoverPose:
        left = cx - sprites.ROVER_W // 2
        mood = self._mood(rover, fx, t)
        alert = bool([a for a in rover.alarms if a.state == "new"]) and int(t * 4) % 2 == 0
        spin = int(t * 18) if rover.stuck else (int(rover.x * 0.9) if rover.velocity > 0 else 0)
        tint = (150, 205, 240) if rover.mode in ("safe", "shielded") else None
        # a soft shadow on the ground beneath the body
        for x in range(left + 6, left + sprites.ROVER_W - 4):
            if 0 <= x < c.w:
                sr = surface[x]
                c.bg[sr][x] = shade(c.bg[sr][x], 0.72)
        arm = None
        floor_px = None
        if rover.job is not None and rover.job.kind == "sample":
            arm = (rover.job.instrument or "camera", t)
            ax = left + sprites.ARM[0] + 4
            floor_px = 2 * surface[min(max(ax, 0), c.w - 1)] - 1
        led = rover_art.LED_COLORS.get(mood if rover.mode == "driving" else rover.mode)
        led = led or LED_COLOR.get(rover.mode)
        pose = rover_art.draw_rover(
            c,
            left,
            surface,
            t,
            spin=spin,
            mood=mood,
            gaze=self._gaze(rover, mood, t),
            dead=rover.mode == "dead",
            led=led,
            tint=tint,
            alert_flash=alert,
            stuck=rover.stuck,
            arm_job=arm,
            sample_floor_px=floor_px,
        )
        c.flush_pixels()
        # a headlight cone after dark
        if rover.daylight < 0.35 and rover.mode != "dead":
            hx = left + sprites.ARM[0] + 6
            for dx in range(24):
                x = hx + dx
                if 0 <= x < c.w:
                    a = 0.32 * (1 - dx / 24)
                    for y in range(max(0, surface[x] - 2), min(c.h, surface[x] + 2)):
                        c.bg[y][x] = lerp(c.bg[y][x], (255, 238, 180), a)
        # signal arcs rise from the dish while a message is on its way
        if fx.comms_pulse > 0:
            ph = int(t * 6) % 4
            for i in range(min(ph, 3)):
                c.put(left + sprites.ANTENNA[0] + 2 + i * 2, pose.head_row + 1 - i % 2, ")", (130, 220, 255))
        if rover.mode == "safe":
            c.text(left + 8, pose.head_row - 1, "SAFE MODE", (130, 220, 255))
        if rover.shield != "off":
            ground = max(surface[min(max(left + x, 0), c.w - 1)] for x in sprites.WHEEL_X)
            self._dome(c, rover, cx, pose.head_row, ground, t, fx)
        else:
            fx.dome = None
        return pose

    def _bubble(self, c: Canvas, fx: EffectsLayer, pose: rover_art.RoverPose) -> None:
        """Rovy's speech bubble, above her head."""
        b = fx.bubble
        if b is None:
            return
        words, lines, cur = b.text.split(), [], ""
        for w in words:
            if len(cur) + len(w) + 1 > 30 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        lines.append(cur)
        width = max(len(sprites.ROVER_NAME), *(len(x) for x in lines)) + 2
        fade = min(1.0, max(0.0, (b.ttl - b.age) / 0.6))
        edge = lerp((40, 50, 70), b.color, fade)
        txt = lerp((70, 70, 80), (245, 245, 250), fade)
        x0 = min(max(pose.head_cx - 4, 1), max(1, c.w - width - 3))
        bottom = pose.head_row - 2
        top = bottom - len(lines) - 1
        bg = (22, 26, 40)
        for y in range(top, bottom + 1):
            for x in range(x0, x0 + width + 2):
                if 0 <= x < c.w and 0 <= y < c.h:
                    c.put(x, y, " ", None, bg)
        c.text(x0 + 1, top, "╭" + "─" * width + "╮", edge, bg)
        c.text(
            x0 + 1, bottom, "╰" + "─" * (width // 2 - 1) + "▾" + "─" * (width - width // 2) + "╯", edge, bg
        )
        c.text(x0 + 2, top, sprites.ROVER_NAME, lerp(edge, (255, 255, 255), 0.3), bg)
        for i, line in enumerate(lines):
            y = top + 1 + i
            c.put(x0 + 1, y, "│", edge, bg)
            c.put(x0 + width + 2, y, "│", edge, bg)
            c.text(x0 + 2, y, line, txt, bg)

    def _dome(self, c: Canvas, rover: Rover, mx: int, top: int, ground_row: int, t: float, fx) -> None:
        """The protective shield: a shimmering dome that grows as it deploys."""
        if rover.shield == "deploying":
            p = 1 - (rover.shield_until - rover.tick_n) / SHIELD_DEPLOY
        elif rover.shield == "retracting":
            p = (rover.shield_until - rover.tick_n) / SHIELD_RETRACT
        else:
            p = 1.0
        p = max(0.1, min(1.0, p))
        rx, ry = int((sprites.ROVER_W / 2 + 6) * p) + 2, int((ground_row - top + 3) * p)
        base = (90, 210, 255)
        glow = 0.7 + 0.2 * math.sin(t * 6) + fx.shield_hit * 0.5
        col = lerp((70, 150, 210), (235, 252, 255), min(1.0, glow))
        fx.dome = (mx, ground_row, rx, ry)  # lets the effects keep hazards out of the bubble
        prev = None
        for dx in range(-rx, rx + 1):
            dy = ry * math.sqrt(max(0.0, 1 - (dx / rx) ** 2))
            row = ground_row - 1 - round(dy)
            slope = abs(dx) / rx
            ch = "-" if slope < 0.5 else ("/" if dx < 0 else "\\") if slope < 0.9 else "|"
            if (dx + int(t * 8)) % 7 == 0 and fx.shield_hit > 0.2:
                ch = "*"
            edge = lerp(col, base, 0.4) if slope > 0.95 else col
            c.put(mx + dx, row, ch, edge)
            if prev is not None and abs(row - prev) > 1:  # keep the outline unbroken
                step = 1 if row > prev else -1
                for yy in range(prev + step, row, step):
                    c.put(mx + dx, yy, "|", edge)
            prev = row

    # ---- markers and HUD badges ---------------------------------------------------------------------

    def _markers(self, c, rover, cam, cx, surface, gy, t) -> None:
        row_i = 0
        for hz in rover.detected_hazards():
            if hz.x is None:
                continue
            dist = hz.x - rover.x
            x0 = self._col(hz.x, cam, cx)
            col = SEV_COLOR[hz.severity]
            if hz.response is None and int(t * 3) % 2:
                col = (255, 255, 255)
            text = f"#{hz.id} {LABELS[hz.kind]} s{hz.severity} {max(0, round(dist))}m"
            row = max(1, gy - 11 - (row_i % 3) * 2)
            row_i += 1
            sub = f"-> {hz.response}" if hz.response else "undecided"
            if hz.awaiting_earth:
                sub = "-> waiting for Earth"
            if hz.kind == "strange_rock" and hz.plan:
                sub = f"-> {hz.plan}"
            x = min(max(x0 - 1, 1), c.w - len(text) - 2)
            arrow = ""
            if x0 >= c.w:
                arrow = ">"
            c.text(x, row, text + arrow, col)
            c.text(x, row + 1, sub, (120, 235, 150) if hz.response or hz.plan else (200, 200, 200))
            if 0 <= x0 < c.w:
                for y in range(row + 2, surface[x0] - 1):
                    c.put(x0, y, "|", shade(col, 0.55))
        y = 1
        for hz in rover.detected_hazards():
            if hz.x is not None:
                continue
            left = max(0, (hz.duration or 0) - (rover.tick_n - hz.start_tick))
            txt = f"[!] #{hz.id} {LABELS[hz.kind]} s{hz.severity}  {left}t"
            if hz.response:
                txt += f"  -> {hz.response}"
            c.text(2, y, txt, SEV_COLOR[hz.severity])
            y += 1
        for at, kind, _, sev in rover.schedule[:4]:
            c.text(
                2,
                y,
                f"    incoming: {LABELS[kind]} s{sev or '?'} in {max(0, at - rover.tick_n) / 10:.0f}s",
                (150, 150, 165),
            )
            y += 1

    # ---- effects and atmosphere ---------------------------------------------------------------------

    def _effects(self, c, fx: EffectsLayer, cam, cx, surface, gy, ground_row) -> None:
        for p in fx.particles:
            if p.space == "world":
                x = self._col(p.x, cam, cx)
                base = surface[x] - 1 if 0 <= x < c.w else gy
                y = base - round(p.y)
            elif p.space == "rover":
                x, y = cx + round(p.x), ground_row - 1 - round(p.y)
            else:
                x, y = round(p.x), round(p.y)
            if 0 <= x < c.w and 0 <= y < c.h:
                col = lerp(p.color, c.bg[y][x], p.age / p.ttl * 0.85)
                c.put(x, y, p.ch, col)
        for lb in fx.labels:
            rise = lb.age * lb.rise
            if lb.space == "rover":
                x, y = (
                    cx + round(lb.x) - len(lb.text) // 2 + 4,
                    ground_row - 1 - round(lb.y + rise) - (sprites.ROVER_ROWS - 5),
                )
            else:
                x, y = round(lb.x), round(lb.y)
            col = lerp(lb.color, (30, 20, 30), max(0.0, (lb.age / lb.ttl - 0.6) / 0.4) * 0.8)
            for i, ch in enumerate(lb.text):
                if ch != " ":
                    c.put(
                        x + i,
                        y,
                        ch,
                        col,
                        shade(c.bg[y][x + i], 0.7) if 0 <= x + i < c.w and 0 <= y < c.h else None,
                    )

    def _fog(self, c: Canvas, cx: int) -> None:
        if self.dust < 0.15:
            return
        fogc = lerp(DUST_HOR, (120, 80, 50), 0.3)
        for x in range(cx + 18, c.w):
            a = min(0.75, (x - cx - 18) / max(c.w - cx - 18, 1) * self.dust * 1.1)
            for y in range(c.h):
                c.bg[y][x] = lerp(c.bg[y][x], fogc, a)
                c.fg[y][x] = lerp(c.fg[y][x], fogc, a * 0.9)
