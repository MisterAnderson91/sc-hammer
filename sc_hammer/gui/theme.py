"""Colours and fonts. One dark theme, sized to read comfortably on a Steam Deck or a TV."""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

C = {
    "bg": "#12151c",
    "header": "#0c0f14",
    "card": "#1a1f28",
    "card_line": "#272e3b",
    "slot": "#222935",
    "slot_hover": "#2a3241",
    "selected": "#1d3554",
    "target": "#173a2b",
    "text": "#e8ebf1",
    "muted": "#9aa3b5",
    "faint": "#606a7d",
    "accent": "#4c9aff",
    "accent_hover": "#70b0ff",
    "on_accent": "#08111f",
    "ok": "#4cc38a",
    "warn": "#e8a33d",
    "warn_bg": "#33280f",
    "danger": "#ef5b5b",
    "danger_hover": "#ff7676",
    "button": "#2b3342",
    "button_hover": "#364052",
    "banner": "#1d2533",
}

# One colour per pairing key, so both ends of a pairing share a dot colour.
PAIR_COLORS = ["#4c9aff", "#f2a541", "#b383ff", "#4cc38a", "#ff6b8b", "#38c6d6", "#e2d266",
               "#ff8a4c", "#8fd14f", "#e07be0"]

CHIP = {"ok": ("#173a2b", "#5fd69b"), "accent": ("#1d3554", "#8cc0ff"), "warn": ("#3a2c10", "#f0b85a")}

PREFERRED_FAMILIES = ("Noto Sans", "Cantarell", "Inter", "Ubuntu", "Segoe UI", "DejaVu Sans",
                      "Helvetica Neue", "Helvetica", "Arial")


class Fonts:
    """Named fonts scaled together, so Ctrl +/- zoom resizes everything at once.

    Sizes are in pixels, not points: Tk turns points into pixels with the DPI the X server
    reports, which on a Steam Deck's small panel is ~216 and makes every font twice too big.
    `scale` is the desktop's own scale setting (Xft.dpi / 96) instead."""

    SIZES = {"base": 14, "small": 13, "bold": 14, "title": 22, "h2": 17, "card": 17, "mono": 13,
             "big": 19}

    def __init__(self, root: tk.Misc, zoom: float = 1.0, scale: float = 1.0):
        families = set(tkfont.families(root))
        family = next((f for f in PREFERRED_FAMILIES if f in families), "TkDefaultFont")
        mono = next((f for f in ("Noto Sans Mono", "DejaVu Sans Mono", "Consolas", "Menlo", "Courier")
                     if f in families), "TkFixedFont")
        self.zoom = zoom
        self.scale = scale
        self.f = {}
        for name in self.SIZES:
            weight = "bold" if name in ("bold", "title", "h2", "card", "big") else "normal"
            fam = mono if name == "mono" else family
            self.f[name] = tkfont.Font(root, family=fam, weight=weight)
        self.apply()

    def apply(self) -> None:
        for name, size in self.SIZES.items():
            self.f[name].configure(size=-max(8, round(size * self.zoom * self.scale)))

    def __getitem__(self, name: str) -> tkfont.Font:
        return self.f[name]

    def px(self, n: float) -> int:
        return round(n * self.zoom * self.scale)
