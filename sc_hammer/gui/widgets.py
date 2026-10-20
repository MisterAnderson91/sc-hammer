"""Small flat widgets. Plain tk (not ttk) so colours look the same on every desktop."""
from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable, Optional

from .theme import C, CHIP, Fonts

STYLES = {
    "primary": ("accent", "accent_hover", "on_accent"),
    "secondary": ("button", "button_hover", "text"),
    "danger": ("danger", "danger_hover", "on_accent"),
    "ghost": (None, "button", "muted"),
}


class Button(tk.Label):
    def __init__(self, parent: tk.Misc, fonts: Fonts, text: str,
                 command: Optional[Callable[[], None]] = None, kind: str = "secondary",
                 small: bool = False, bg: Optional[str] = None):
        bg_key, hover_key, fg_key = STYLES[kind]
        self._bg = C[bg_key] if bg_key else (bg or parent.cget("bg"))
        self._hover = C[hover_key]
        self._fg = C[fg_key]
        self._command = command
        self._enabled = True
        super().__init__(parent, text=text, bg=self._bg, fg=self._fg,
                         font=fonts["small" if small else "bold"],
                         padx=fonts.px(10 if small else 14), pady=fonts.px(4 if small else 7),
                         cursor="hand2")
        self.bind("<Enter>", lambda e: self._enabled and self.configure(bg=self._hover))
        self.bind("<Leave>", lambda e: self.configure(bg=self._bg))
        self.bind("<ButtonRelease-1>", self._click)

    def _click(self, event: tk.Event) -> None:
        w = self.winfo_containing(event.x_root, event.y_root)
        if self._enabled and w is self and self._command:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(fg=self._fg if enabled else C["faint"], cursor="hand2" if enabled else "")


def chip(parent: tk.Misc, fonts: Fonts, text: str, style: str, bg: str) -> tk.Label:
    back, fore = CHIP[style]
    return tk.Label(parent, text=text, bg=back, fg=fore, font=fonts["small"],
                    padx=fonts.px(8), pady=fonts.px(1))


class Dot(tk.Canvas):
    def __init__(self, parent: tk.Misc, fonts: Fonts, color: Optional[str], bg: str,
                 unknown: bool = False):
        size = fonts.px(14)
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)
        m = max(1, fonts.px(2))
        if unknown:
            self.create_text(size / 2, size / 2, text="?", fill=C["warn"], font=fonts["small"])
        elif color:
            self.create_oval(m, m, size - m, size - m, fill=color, outline=color)
        else:
            self.create_oval(m, m, size - m, size - m, outline=C["faint"], width=max(1, fonts.px(1.5)))

    def set_bg(self, bg: str) -> None:
        self.configure(bg=bg)


class ScrollArea(tk.Frame):
    """A vertically scrolling frame; put content in .inner."""

    def __init__(self, parent: tk.Misc, bg: str):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                troughcolor=bg, bg=C["button"], activebackground=C["button_hover"],
                                highlightthickness=0, bd=0, width=12)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._sync)
        self.canvas.bind("<Configure>", self._resize)

    def _sync(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize(self, e: tk.Event) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)

    def scroll(self, units: int) -> None:
        if self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")

    def bind_wheel(self, root: tk.Misc) -> None:
        if sys.platform.startswith("linux"):
            root.bind_all("<Button-4>", lambda e: self.scroll(-3), add="+")
            root.bind_all("<Button-5>", lambda e: self.scroll(3), add="+")
        else:
            root.bind_all("<MouseWheel>",
                          lambda e: self.scroll(-1 if e.delta > 0 else 1), add="+")


def auto_wrap(label: tk.Label, start: int = 200) -> tk.Label:
    """Wrap a label's text to whatever width its geometry manager gives it (grid sticky "ew" or
    pack fill "x"), instead of a fixed wraplength that clips or wastes space."""
    label.configure(wraplength=start)

    def fit(e: tk.Event) -> None:
        w = e.width - 2 * label.winfo_pixels(label.cget("padx"))
        if w > 20 and abs(label.winfo_pixels(label.cget("wraplength")) - w) > 2:
            label.configure(wraplength=w)

    label.bind("<Configure>", fit, add="+")
    return label


def bind_tree(widget: tk.Misc, sequence: str, handler) -> None:
    """Bind on a widget and all its descendants (Tk doesn't propagate mouse events to parents)."""
    widget.bind(sequence, handler, add="+")
    for child in widget.winfo_children():
        bind_tree(child, sequence, handler)


def set_tree_bg(widget: tk.Misc, bg: str, skip=()) -> None:
    for w in [widget] + list(_descendants(widget)):
        if w in skip or getattr(w, "keep_bg", False):
            continue
        try:
            w.configure(bg=bg)
        except tk.TclError:
            pass


def _descendants(widget: tk.Misc):
    for child in widget.winfo_children():
        yield child
        yield from _descendants(child)
