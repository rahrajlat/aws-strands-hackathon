"""Art constants. The rover is drawn as pixel art in `rover_art.py` (two pixels per text cell,
using half-block characters).

Rovy is inspired by a small hobby robot: a matte-black chassis, a face made of two big silver
ultrasonic-sensor "eyes" with a tiny OLED screen above them, a red circuit board with an antenna
on the back, a gold logo, ridged black wheels and two glowing LED lights. The game scrolls
sideways, so the chassis and its three wheels are seen from the side (the wheels follow the
terrain on a rocker-bogie suspension) while the head faces the viewer, so the face is always
readable."""

from __future__ import annotations

ROVER_NAME = "Rovy"

# geometry in pixels, relative to the rover's left edge and the top of its head
ROVER_W = 38
BODY_PX = 17  # head + chassis, in pixel rows
WHEEL_PX = 6
STRUT_PX = 2
ROVER_ROWS = (BODY_PX + STRUT_PX + WHEEL_PX + 1) // 2  # text rows the whole rover occupies
WHEEL_X = (6, 17, 28)  # wheel centres: rear, middle, front
HEAD = (18, 0, 30, 10)  # x0, y0, x1, y1 of the head
OLED = (21, 1, 28, 3)  # the little screen
EYE_X = (19, 25)  # left edge of each 5-pixel eye
EYE_Y = 5
CHASSIS = (5, 11, 33, 16)
PCB = (5, 8, 16, 10)
ANTENNA = (7, 2)  # x, y of the antenna tip (signal arcs rise from here)
LEDS = ((25, 14), (30, 14))  # centres of the two glowing lights
ARM = (34, 13)  # where the sampling arm leaves the chassis
STRUT_X = (9, 17, 25)  # where the struts meet the chassis

STATION = [
    "   |>          ",
    "   |           ",
    "  .--'''--.    ",
    " /  [] []  \\   ",
    "|   SITE    |  ",
    "|___________|  ",
]
