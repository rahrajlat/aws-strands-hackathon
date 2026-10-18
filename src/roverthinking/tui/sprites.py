"""Art constants for the character-based rover Rovy.

Rovy is 14 chars wide × 7 text rows tall, drawn with Unicode box-drawing
characters and symbols. No pixel layer is used.
"""

from __future__ import annotations

ROVER_NAME = "Rovy"

ROVER_W = 14
BODY_PX = 12       # rows 0-5 (head + chassis) = 6 rows × 2 px = 12 px
WHEEL_PX = 2       # wheel row = 1 text row = 2 px
STRUT_PX = 0
ROVER_ROWS = (BODY_PX + STRUT_PX + WHEEL_PX + 1) // 2   # = 7

WHEEL_X = (3, 7, 11)       # wheel column offsets from rover left
HEAD = (3, 0, 10, 2)       # head bounding box: x0, y0, x1, y1
OLED = (6, 1, 6, 1)        # single-char OLED indicator at column 6, row 1
EYE_X = (5, 8)             # column offsets of left / right eye
EYE_Y = 2
CHASSIS = (0, 3, 13, 5)    # chassis bounding box
PCB = (0, 3, 3, 3)         # antenna signal-arc anchor region
ANTENNA = (2, 0)           # column, row of signal origin (used by scene for comms arcs)
LEDS = ((11, 4),)          # single status LED position
ARM = (8, 4)               # sampling arm exit point (ARM[0] + 6 ≈ right edge → headlight origin)
STRUT_X = (3, 7, 11)       # strut positions under chassis (same as WHEEL_X)

STATION = [
    "   |>          ",
    "   |           ",
    "  .--'''--.    ",
    " /  [] []  \\   ",
    "|   SITE    |  ",
    "|___________|  ",
]
