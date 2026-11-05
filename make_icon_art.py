#!/usr/bin/env python3
"""
Draw icon.png -- the Max2OSC icon -- from scratch.

    python3 make_icon_art.py            # writes icon.png at 1024x1024
    python3 make_icon_art.py out.png 512

The mark is a head seen from above: a ring, a triangle for the nose showing
which way it faces, and an arc beneath it for yaw. Three shapes, so it still
reads at 16 pixels.

Shapes are drawn as signed distance fields, which gives clean antialiasing
from a single sample per pixel, and the PNG is written by hand -- so this
needs nothing but the standard library. Edit the palette and geometry below.
"""
import math
import struct
import sys
import zlib

# --- palette ---------------------------------------------------------------
BG  = (0x1E, 0x24, 0x30)   # background squircle, slate
FG  = (0xF2, 0xF0, 0xEA)   # head ring and nose, warm white
ACC = (0xF0, 0xA0, 0x3C)   # yaw arc, amber

# --- geometry, in a coordinate space running -1..1 across the image --------
CORNER_RADIUS = 0.30       # of the background squircle
BODY_HALF     = 0.92       # half-width of the squircle
HEAD_Y        = -0.04      # the head sits slightly above centre
HEAD_RADIUS   = 0.42
HEAD_STROKE   = 0.055
NOSE_OFFSET   = 0.52       # how far the nose sits from centre
NOSE_SIZE     = 0.17
ARC_RADIUS    = 0.76
ARC_STROKE    = 0.045
ARC_GAP       = 0.30       # radians trimmed from each end of the arc
ARC_CAP       = 0.085      # radius of the round cap at each end


def _mix(base, over, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(base, over))


def _squircle(x, y, half, radius):
    dx, dy = abs(x) - (half - radius), abs(y) - (half - radius)
    return math.hypot(max(dx, 0), max(dy, 0)) + min(max(dx, dy), 0) - radius


def _coverage(distance, width):
    """Signed distance to how much of the pixel the shape covers."""
    return max(0.0, min(1.0, 0.5 - distance / width))


def render(size):
    rows = []
    px_width = 2.0 / size * 2        # one pixel, in coordinate-space units
    for row_index in range(size):
        row = bytearray()
        y = (row_index + 0.5) / size * 2 - 1
        for col_index in range(size):
            x = (col_index + 0.5) / size * 2 - 1

            alpha = _coverage(_squircle(x, y, BODY_HALF, CORNER_RADIUS), px_width)
            if alpha <= 0:
                row += b"\x00\x00\x00\x00"
                continue
            colour = BG

            ring = abs(math.hypot(x, y - HEAD_Y) - HEAD_RADIUS) - HEAD_STROKE
            on_ring = _coverage(ring, px_width) * alpha
            if on_ring > 0:
                colour = _mix(colour, FG, on_ring)

            nose_y = y + NOSE_OFFSET
            nose = max(abs(x) * 0.86 + nose_y * 0.5, -nose_y - NOSE_SIZE)
            on_nose = _coverage(nose, px_width) * alpha
            if on_nose > 0:
                colour = _mix(colour, FG, on_nose)

            angle = math.atan2(y - HEAD_Y, x)
            within = 1.0 if ARC_GAP < angle < math.pi - ARC_GAP else 0.0
            arc = abs(math.hypot(x, y - HEAD_Y) - ARC_RADIUS) - ARC_STROKE
            on_arc = _coverage(arc, px_width) * within * alpha
            if on_arc > 0:
                colour = _mix(colour, ACC, on_arc)

            for end in (ARC_GAP, math.pi - ARC_GAP):
                cap_x = ARC_RADIUS * math.cos(end)
                cap_y = ARC_RADIUS * math.sin(end) + HEAD_Y
                on_cap = _coverage(
                    math.hypot(x - cap_x, y - cap_y) - ARC_CAP, px_width) * alpha
                if on_cap > 0:
                    colour = _mix(colour, ACC, on_cap)

            row += bytes((*colour, round(alpha * 255)))
        rows.append(bytes(row))
    return rows


# --- menu bar variants -----------------------------------------------------
# The menu bar needs its own drawing. A template image is rendered from the
# alpha channel alone, so the opaque background squircle of the app icon
# would come out as a solid block. These carry the mark in alpha instead,
# with heavier strokes so it survives at 18 points.

MB_HEAD_RADIUS = 0.46
MB_HEAD_STROKE = 0.115
MB_NOSE_OFFSET = 0.60
MB_NOSE_SIZE   = 0.20
MB_ARC_RADIUS  = 0.86
MB_ARC_STROKE  = 0.105
MB_ARC_GAP     = 0.55


def render_menubar(size, active):
    """The mark alone, on transparency. `active` adds the yaw arc."""
    rows = []
    px = 2.0 / size * 2
    for row_index in range(size):
        row = bytearray()
        y = (row_index + 0.5) / size * 2 - 1
        for col_index in range(size):
            x = (col_index + 0.5) / size * 2 - 1

            ring = abs(math.hypot(x, y - HEAD_Y) - MB_HEAD_RADIUS) - MB_HEAD_STROKE
            alpha = _coverage(ring, px)

            nose_y = y + MB_NOSE_OFFSET
            nose = max(abs(x) * 0.86 + nose_y * 0.5, -nose_y - MB_NOSE_SIZE)
            alpha = max(alpha, _coverage(nose, px))

            if active:
                angle = math.atan2(y - HEAD_Y, x)
                within = 1.0 if MB_ARC_GAP < angle < math.pi - MB_ARC_GAP else 0.0
                arc = abs(math.hypot(x, y - HEAD_Y) - MB_ARC_RADIUS) - MB_ARC_STROKE
                alpha = max(alpha, _coverage(arc, px) * within)
                for end in (MB_ARC_GAP, math.pi - MB_ARC_GAP):
                    cx = MB_ARC_RADIUS * math.cos(end)
                    cy = MB_ARC_RADIUS * math.sin(end) + HEAD_Y
                    alpha = max(alpha, _coverage(
                        math.hypot(x - cx, y - cy) - MB_ARC_STROKE * 1.5, px))

            # Template images take their shape from alpha; macOS supplies the
            # colour, so the pixels themselves are plain black.
            row += bytes((0, 0, 0, round(max(0.0, min(1.0, alpha)) * 255)))
        rows.append(bytes(row))
    return rows


def write_png(path, rows, size):
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + row for row in rows)   # filter type 0 per scanline
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)


def main():
    if "--menubar" in sys.argv:
        for name, active in (("menubar_idle.png", False),
                             ("menubar_active.png", True)):
            written = write_png(name, render_menubar(36, active), 36)
            print(f"wrote {name}  36x36  {written} bytes")
        return
    path = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
    written = write_png(path, render(size), size)
    print(f"wrote {path}  {size}x{size}  {written / 1024:.0f} KB")
    print("now run ./make_icons.sh to regenerate the .icns and .ico")


if __name__ == "__main__":
    main()
