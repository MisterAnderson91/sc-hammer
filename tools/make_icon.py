#!/usr/bin/env python3
"""Draws the app icon (a hammer coming down on a Steam Controller, with wireless arcs) as PNGs,
using only the standard library.

    python3 tools/make_icon.py      # writes sc_hammer/assets/icon-{32,64,128,256}.png
"""
import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(__file__), "..", "sc_hammer", "assets")

BG_TOP = (28, 38, 58)
BG_BOTTOM = (14, 18, 28)
BLUE = (76, 154, 255)
LIGHT = (225, 235, 250)
ORANGE = (242, 165, 65)
STEEL = (200, 210, 225)
STEEL_DARK = (130, 145, 165)
WOOD = (196, 132, 76)
WOOD_DARK = (150, 96, 52)


def in_round_square(x, y, r=0.22):
    cx = min(max(x, r), 1 - r)
    cy = min(max(y, r), 1 - r)
    return math.hypot(x - cx, y - cy) <= r


def rect(x, y, cx, cy, hw, hh, ang, r=0.0):
    """(inside, u, v) for a rotated rounded rectangle: half sizes hw/hh, angle in degrees."""
    a = math.radians(ang)
    dx, dy = x - cx, y - cy
    u = dx * math.cos(a) + dy * math.sin(a)
    v = -dx * math.sin(a) + dy * math.cos(a)
    qu, qv = abs(u) - (hw - r), abs(v) - (hh - r)
    outside = math.hypot(max(qu, 0), max(qv, 0))
    return outside + min(max(qu, qv), 0) <= r, u, v


def hammer(x, y, hx, hy, ang, s):
    """Hammer with its head centred on (hx, hy) and the handle pointing along ang degrees."""
    a = math.radians(ang)
    hcx, hcy = hx + math.cos(a) * 0.20 * s, hy + math.sin(a) * 0.20 * s
    inside, _, v = rect(x, y, hcx, hcy, 0.20 * s, 0.035 * s, ang, 0.03 * s)
    if inside:
        return WOOD if v < 0.012 * s else WOOD_DARK
    inside, u, _ = rect(x, y, hx, hy, 0.065 * s, 0.13 * s, ang, 0.02 * s)
    if inside:
        return STEEL if u < 0.02 * s else STEEL_DARK
    return None


def body(x, y, t):
    """Controller silhouette shrunk by t: a wide rounded body plus two round grips."""
    inside, _, _ = rect(x, y, 0.50, 0.555, 0.34 - t, 0.155 - t, 0, max(0.12 - t, 0.005))
    if inside:
        return True
    return any(math.hypot(x - gx, y - 0.715) <= 0.13 - t for gx in (0.27, 0.73))


def controller(x, y):
    """Outline with two thumbsticks, two square trackpads and the Steam button."""
    t = 0.028
    if body(x, y, 0) and not body(x, y, t):
        return BLUE
    if not body(x, y, t):
        return None
    for sx in (0.37, 0.63):
        if abs(math.hypot(x - sx, y - 0.475) - 0.04) <= 0.012:
            return LIGHT
    for tx in (0.33, 0.67):
        outer, _, _ = rect(x, y, tx, 0.605, 0.056, 0.056, 0, 0.018)
        inner, _, _ = rect(x, y, tx, 0.605, 0.042, 0.042, 0, 0.01)
        if outer and not inner:
            return LIGHT
    if math.hypot(x - 0.50, y - 0.52) <= 0.022:
        return BLUE
    return None


def shade(x, y):
    """Colour and alpha of one sample point, coordinates 0..1."""
    if not in_round_square(x, y):
        return (0, 0, 0, 0)
    c = hammer(x, y, 0.30, 0.32, -55, 0.85) or controller(x, y)
    if c is None:
        # wireless arcs off the controller's top right
        d = math.hypot(x - 0.62, y - 0.52)
        ang = math.degrees(math.atan2(0.52 - y, x - 0.62))
        if 35 <= ang <= 80 and any(abs(d - r) <= 0.021 for r in (0.22, 0.29)):
            c = ORANGE
    if c is None:
        c = tuple(BG_TOP[i] * (1 - y) + BG_BOTTOM[i] * y for i in range(3))
    return tuple(c) + (255,)


def render(size, ss=4):
    rows = []
    for j in range(size):
        row = bytearray([0])
        for i in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sj in range(ss):
                for si in range(ss):
                    r, g, b, a = shade((i + (si + 0.5) / ss) / size, (j + (sj + 0.5) / ss) / size)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            if acc[3]:
                row += bytes(round(acc[k] / acc[3]) for k in range(3))
            else:
                row += bytes(3)
            row.append(round(acc[3] / (ss * ss)))
        rows.append(bytes(row))
    return png(size, size, b"".join(rows))


def png(w, h, raw):
    def chunk(kind, data):
        c = struct.pack(">I", len(data)) + kind + data
        return c + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    os.makedirs(OUT, exist_ok=True)
    for size in (32, 64, 128, 256):
        with open(os.path.join(OUT, "icon-%d.png" % size), "wb") as f:
            f.write(render(size))
        print("wrote icon-%d.png" % size)


if __name__ == "__main__":
    main()
