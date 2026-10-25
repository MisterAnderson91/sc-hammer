"""Main window: receivers and controllers as cards of slots, an action bar, drag and drop."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import webbrowser
from typing import Callable, Dict, List, Optional

from .. import APP_NAME, TAGLINE, __version__
from .. import actions as a
from .. import updates
from ..describe import Describer, Names
from ..model import (CONTROLLER_SLOT_COMBOS, UNKNOWN, USED, Controller, Problem, Receiver,
                     Scanner, Snapshot, slot_name)
from ..store import Store
from . import dialogs
from .theme import C, PAIR_COLORS, Fonts
from .widgets import Button, Dot, ScrollArea, auto_wrap, bind_tree, chip, set_tree_bg

ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
DRAG_THRESHOLD = 8
MAX_CARDS_ACROSS = 2  # per section: wider screens get wider cards, not more columns

ACTION_BUTTONS = [
    (a.MOVE, "Move…", "Choose an empty slot to move it to. On another receiver or controller it "
                      "gets a fresh pairing."),
    (a.SWAP, "Swap…", "Choose a slot on the same device to trade places with."),
    (a.PAIR, "Pair with…", "Choose the slot to make a fresh pairing with."),
]


class Worker:
    """Runs device I/O off the UI thread, one job at a time, in order."""

    def __init__(self):
        self.jobs: "queue.Queue" = queue.Queue()
        self.results: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            fn, done, fail = self.jobs.get()
            try:
                self.results.put((done, fn(self._progress)))
            except Exception as e:  # reported in the UI, never kills the worker
                self.results.put((fail, e))

    def _progress(self, text: str) -> None:
        self.results.put(("progress", text))

    def submit(self, fn, done, fail) -> None:
        self.jobs.put((fn, done, fail))


class SlotRow(tk.Frame):
    def __init__(self, parent: tk.Misc, app: "App", ref: a.SlotRef, label: str):
        super().__init__(parent, bg=C["slot"], highlightthickness=app.fonts.px(2),
                         highlightbackground=C["slot"], highlightcolor=C["slot"],
                         padx=app.fonts.px(10), pady=app.fonts.px(7), cursor="hand2")
        self.app = app
        self.ref = ref
        f = app.fonts
        text = app.describer.receiver_slot(*app.lookup(ref)) if ref.kind == a.RECEIVER \
            else app.describer.controller_slot(*app.lookup(ref))
        self.state = text.state
        color_idx = app.describer.color_index(text.key)
        color = PAIR_COLORS[color_idx % len(PAIR_COLORS)] if color_idx is not None else None
        self.dot = Dot(self, f, color, C["slot"], unknown=text.state == UNKNOWN)
        self.dot.grid(row=0, column=0, rowspan=3, padx=(0, f.px(10)))
        tk.Label(self, text=label, bg=C["slot"], fg=C["muted"], font=f["small"], width=6,
                 anchor="w").grid(row=0, column=1, rowspan=3, sticky="w")
        main_fg = C["text"] if text.state == USED else C["faint"]
        auto_wrap(tk.Label(self, text=text.main, bg=C["slot"], fg=main_fg,
                           font=f["bold"] if text.state == USED else f["base"], anchor="w",
                           justify="left"), f.px(150)).grid(row=0, column=2, sticky="ew")
        sub = text.sub
        if app.show_keys and text.key:
            sub += "   ·   key " + text.key.hex()
        auto_wrap(tk.Label(self, text=sub, bg=C["slot"], fg=C["muted"], font=f["small"], anchor="w",
                           justify="left"), f.px(200)).grid(row=1, column=2, columnspan=2, sticky="ew")
        if ref.kind == a.CONTROLLER and ref.index < len(CONTROLLER_SLOT_COMBOS) and \
                not app.lookup(ref)[0].reversepuck:
            auto_wrap(tk.Label(self, text="Turn on with %s to use this slot" %
                               CONTROLLER_SLOT_COMBOS[ref.index], bg=C["slot"], fg=C["faint"],
                               font=f["small"], anchor="w", justify="left"),
                      f.px(200)).grid(row=2, column=2, columnspan=2, sticky="ew")
        chips = tk.Frame(self, bg=C["slot"])
        chips.grid(row=0, column=3, sticky="e")
        for t, style in text.chips:
            c = chip(chips, f, t, style, C["slot"])
            c.keep_bg = True
            c.pack(side="left", padx=(f.px(4), 0))
        self.columnconfigure(2, weight=1)
        for seq, fn in (("<ButtonPress-1>", app.on_press), ("<B1-Motion>", app.on_motion),
                        ("<ButtonRelease-1>", app.on_release), ("<Button-3>", app.on_context),
                        ("<Enter>", lambda e: self.paint(hover=True)),
                        ("<Leave>", lambda e: self.paint())):
            bind_tree(self, seq, fn)
        if sys.platform == "darwin":
            bind_tree(self, "<Button-2>", app.on_context)

    def paint(self, hover: bool = False) -> None:
        app = self.app
        bg, border = C["slot"], C["slot"]
        if app.pick and self.ref in app.targets:
            bg, border = C["target"], C["ok"]
        elif app.drag and self.ref in app.targets:
            bg, border = C["target"], C["ok"]
        elif self.ref == app.selected:
            bg, border = C["selected"], C["accent"]
        elif hover and not app.busy:
            bg = C["slot_hover"]
        if app.pick and self.ref == app.pick[1]:
            bg, border = C["selected"], C["accent"]
        self.configure(highlightbackground=border, highlightcolor=border)
        set_tree_bg(self, bg)


class App:
    def __init__(self, root: tk.Tk, transport, store: Store, demo: bool, zoom: float,
                 scale: float = 1.0):
        self.root = root
        self.transport = transport
        self.store = store
        self.demo = demo
        self.fonts = Fonts(root, zoom, scale)
        self.scanner = Scanner(transport) if transport else None
        self.worker = Worker()
        self.snap = Snapshot()
        self.describer = Describer(self.snap, Names(store, self.snap))
        self.selected: Optional[a.SlotRef] = None
        self.pick: Optional[tuple] = None          # (op, src) while choosing a destination
        self.targets: set = set()
        self.drag: Optional[dict] = None
        self.press: Optional[dict] = None
        self.busy = False
        self.modal = False
        self.show_keys = False
        self.rows: Dict[a.SlotRef, SlotRow] = {}
        self.signature = None
        self._status_job = None
        self.update_version: Optional[str] = None  # a newer release, once the check finds one
        self._build()
        self.root.after(40, self._poll_worker)
        if updates.claim_check(__version__):  # once per run
            threading.Thread(target=self._check_for_update, daemon=True).start()
        if self.scanner:
            self.refresh()
            self.root.after(1500, self._watch_hotplug)
        else:
            self.render()

    # ---------- layout ----------

    def _build(self) -> None:
        f = self.fonts
        root = self.root
        root.configure(bg=C["bg"])
        header = tk.Frame(root, bg=C["header"], padx=f.px(20), pady=f.px(12))
        header.pack(fill="x")
        self.icon_img = None
        try:
            self.icon_img = tk.PhotoImage(file=os.path.join(ASSETS, "icon-32.png"))
            tk.Label(header, image=self.icon_img, bg=C["header"]).pack(side="left", padx=(0, f.px(10)))
        except tk.TclError:
            pass
        titles = tk.Frame(header, bg=C["header"])
        titles.pack(side="left")
        tk.Label(titles, text=APP_NAME, bg=C["header"], fg=C["text"],
                 font=f["title"]).pack(anchor="w")
        tk.Label(titles, text=TAGLINE + (" · demo" if self.demo else ""), bg=C["header"],
                 fg=C["muted"], font=f["small"]).pack(anchor="w")
        for text, cmd, kind in (("Help", self.on_help, "ghost"),
                                ("Backups", self.on_backups, "secondary"),
                                ("Refresh", self.refresh, "secondary")):
            Button(header, f, text, cmd, kind, bg=C["header"]).pack(side="right", padx=(f.px(8), 0))
        self.keys_btn = Button(header, f, "Show keys", self.toggle_keys, "ghost", bg=C["header"])
        self.keys_btn.pack(side="right", padx=(f.px(8), 0))

        self.banner = tk.Frame(root, bg=C["bg"])
        self.banner.pack(fill="x")

        # Bottom bars are packed before the scroll area so they keep their space when the window shrinks.
        self.status = tk.Label(root, text="", bg=C["header"], fg=C["muted"], font=f["small"],
                               anchor="w", padx=f.px(20), pady=f.px(6))
        self.status.pack(side="bottom", fill="x")
        self.actionbar = tk.Frame(root, bg=C["card"], padx=f.px(20), pady=f.px(14),
                                  highlightthickness=1, highlightbackground=C["card_line"])
        self.actionbar.pack(side="bottom", fill="x")

        self.scroll = ScrollArea(root, C["bg"])
        self.scroll.pack(fill="both", expand=True)
        self.scroll.bind_wheel(root)
        self.scroll.canvas.bind("<Configure>", lambda e: self._reflow(), add="+")
        self.content = self.scroll.inner

        root.bind("<F5>", lambda e: self.refresh())
        root.bind("<Escape>", lambda e: self.cancel())
        root.bind("<Delete>", lambda e: self.selected and self.start(a.ERASE, self.selected))
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            root.bind(seq, lambda e: self.zoom(1.1))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            root.bind(seq, lambda e: self.zoom(1 / 1.1))
        root.bind("<Control-0>", lambda e: self.zoom(None))

    def lookup(self, ref: a.SlotRef):
        return a.slot_of(self.snap, ref)

    def render(self) -> None:
        f = self.fonts
        self.describer = Describer(self.snap, Names(self.store, self.snap))
        refs = self._all_refs()
        if self.selected not in refs:
            self.selected = None
        if self.pick and self.pick[1] not in refs:
            self.pick = None
        self._compute_targets()
        for w in self.content.winfo_children():
            w.destroy()
        self.rows = {}
        self.cards: List[tk.Frame] = []
        self._render_banner()
        pad = f.px(20)
        gap = f.px(8)
        self.page = tk.Frame(self.content, bg=C["bg"])
        self.page.pack(fill="x", padx=pad - gap, pady=(f.px(6), pad))
        self._layout_key = None

        self.rsec, self.receiver_grid = self._section(
            "Receivers", "Pucks and Steam Machine receivers. Each one remembers up to 4 controllers.")
        for r in self.snap.receivers:
            self._receiver_card(self.receiver_grid, r)
        if not self.snap.receivers:
            self._empty_card(self.receiver_grid, "No receivers found",
                             "Plug in a Steam Controller Puck or an OpenPuck (in Steam mode). It "
                             "shows up here on its own.")

        self.csec, self.controller_grid = self._section(
            "Controllers", "Each controller remembers up to 2 receivers. Plug a controller in with "
            "USB to change its slots.")
        for c in self.snap.controllers:
            self._controller_card(self.controller_grid, c)
        if not self.snap.controllers:
            self._empty_card(self.controller_grid, "No controllers found",
                             "Turn on a controller that's paired with a receiver, or plug one in "
                             "with a USB cable.")
        self._reflow()
        self._render_actionbar()

    def _all_refs(self) -> set:
        refs = set()
        for r in self.snap.receivers:
            refs.update(a.SlotRef(a.RECEIVER, r.serial, s.index) for s in r.slots)
        for c in self.snap.controllers:
            refs.update(a.SlotRef(a.CONTROLLER, c.serial, s.index) for s in c.slots)
        return refs

    def _section(self, title: str, sub: str):
        f = self.fonts
        sec = tk.Frame(self.page, bg=C["bg"])
        tk.Label(sec, text=title, bg=C["bg"], fg=C["text"], font=f["h2"],
                 anchor="w").pack(fill="x", padx=f.px(8), pady=(f.px(12), 0))
        auto_wrap(tk.Label(sec, text=sub, bg=C["bg"], fg=C["muted"], font=f["small"], anchor="w",
                           justify="left")).pack(fill="x", padx=f.px(8), pady=(0, f.px(2)))
        grid = tk.Frame(sec, bg=C["bg"])
        grid.pack(fill="x")
        return sec, grid

    def _card(self, parent: tk.Frame) -> tk.Frame:
        f = self.fonts
        card = tk.Frame(parent, bg=C["card"], padx=f.px(14), pady=f.px(12),
                        highlightthickness=1, highlightbackground=C["card_line"])
        self.cards.append(card)
        return card

    def _card_header(self, card: tk.Frame, title: str, sub: str, serial: str,
                     extra: Optional[Callable[[tk.Frame], None]] = None) -> None:
        f = self.fonts
        head = tk.Frame(card, bg=C["card"])
        head.pack(fill="x", pady=(0, f.px(8)))
        auto_wrap(tk.Label(head, text=title, bg=C["card"], fg=C["text"], font=f["card"],
                           anchor="w", justify="left"), f.px(150)).grid(row=0, column=0, sticky="ew")
        auto_wrap(tk.Label(head, text=sub, bg=C["card"], fg=C["muted"], font=f["small"],
                           anchor="w", justify="left")).grid(row=1, column=0, sticky="ew")
        btns = tk.Frame(head, bg=C["card"])
        btns.grid(row=0, column=1, sticky="ne")
        head.grid_slaves(row=1, column=0)[0].grid_configure(columnspan=2)
        head.columnconfigure(0, weight=1)
        if extra:
            extra(btns)
        Button(btns, f, "Rename", lambda: self.rename(serial), "ghost", small=True,
               bg=C["card"]).pack(side="right")

    def _receiver_card(self, parent: tk.Frame, r: Receiver) -> None:
        card = self._card(parent)
        title, sub = self.describer.receiver_title(r)
        self._card_header(card, title, sub, r.serial)
        for s in r.slots:
            self._slot_row(card, a.SlotRef(a.RECEIVER, r.serial, s.index))

    def _controller_card(self, parent: tk.Frame, c: Controller) -> None:
        f = self.fonts
        card = self._card(parent)
        title, sub = self.describer.controller_title(c)

        def extra(btns: tk.Frame) -> None:
            if c.reversepuck:
                return  # picks up changes straight away and never goes wireless
            if c.usb:
                Button(btns, f, "Restart", lambda: self.restart_controller(c.serial), "ghost",
                       small=True, bg=C["card"]).pack(side="right")
                Button(btns, f, "Go wireless", lambda: self.go_wireless(c.serial), "ghost",
                       small=True, bg=C["card"]).pack(side="right")
            else:
                Button(btns, f, "Disconnect wireless", lambda: self.turn_off_controller(c.serial),
                       "ghost",
                       small=True, bg=C["card"]).pack(side="right")

        self._card_header(card, title, sub, c.serial, extra)
        for s in c.slots:
            self._slot_row(card, a.SlotRef(a.CONTROLLER, c.serial, s.index))
        if not c.usb:
            auto_wrap(tk.Label(card, text="Plug this controller in with USB to change its slots.",
                               bg=C["card"], fg=C["faint"], font=f["small"], anchor="w",
                               justify="left")).pack(fill="x", pady=(f.px(6), 0))

    def _slot_row(self, card: tk.Frame, ref: a.SlotRef) -> None:
        if ref.kind == a.CONTROLLER and not self.lookup(ref)[0].reversepuck:
            label = slot_name(True, ref.index).split()[0].capitalize()
        else:
            label = "Slot %d" % (ref.index + 1)
        row = SlotRow(card, self, ref, label)
        row.pack(fill="x", pady=self.fonts.px(3))
        row.paint()
        self.rows[ref] = row

    def _empty_card(self, parent: tk.Frame, title: str, text: str) -> None:
        f = self.fonts
        card = self._card(parent)
        tk.Label(card, text=title, bg=C["card"], fg=C["text"], font=f["card"],
                 anchor="w").pack(fill="x")
        auto_wrap(tk.Label(card, text=text, bg=C["card"], fg=C["muted"], font=f["base"], anchor="w",
                           justify="left")).pack(fill="x", pady=(f.px(4), 0))

    def _card_min_width(self) -> int:
        """Narrowest a card can be without its slot rows getting cramped, from real text sizes."""
        f = self.fonts
        row = (f["small"].measure("Slot 4") + f["bold"].measure("Controller 00000")
               + f["small"].measure("Connected") + f["small"].measure("In use") + f.px(110))
        head = (f["card"].measure("Controller 00000") + f["small"].measure("Rename")
                + f["small"].measure("Restart") + f["small"].measure("Go wireless") + f.px(90))
        head = max(head, f["card"].measure("Controller 00000") + f["small"].measure("Rename")
                   + f["small"].measure("Disconnect wireless") + f.px(70))
        return max(row, head) + f.px(30)

    def _reflow(self) -> None:
        """Lay the cards out for the current width. Receivers and controllers sit side by side
        when there's room for at least two cards across, so a single receiver doesn't leave most
        of the window empty."""
        width = self.scroll.canvas.winfo_width()
        if width <= 1 or not getattr(self, "page", None) or not self.page.winfo_exists():
            return
        f = self.fonts
        gap = f.px(8)
        across = max(1, (width - f.px(24)) // (self._card_min_width() + 2 * gap))
        nr = max(1, len(self.receiver_grid.winfo_children()))
        nc = max(1, len(self.controller_grid.winfo_children()))
        side = across >= 2
        if side:
            rc = max(1, min(nr, MAX_CARDS_ACROSS, across // 2))
            cc = max(1, min(nc, MAX_CARDS_ACROSS, across - rc))
            rc = max(1, min(nr, MAX_CARDS_ACROSS, across - cc))
        else:
            rc = cc = 1
        key = (side, rc, cc, nr, nc)
        if key == self._layout_key:
            return
        self._layout_key = key
        for i in range(2):
            self.page.columnconfigure(i, weight=0, uniform="")
        if side:
            self.page.columnconfigure(0, weight=rc, uniform="sections")
            self.page.columnconfigure(1, weight=cc, uniform="sections")
            self.rsec.grid(row=0, column=0, sticky="new")
            self.csec.grid(row=0, column=1, sticky="new")
        else:
            self.page.columnconfigure(0, weight=1)
            self.rsec.grid(row=0, column=0, sticky="new")
            self.csec.grid(row=1, column=0, sticky="new")
        for grid, cols in ((self.receiver_grid, rc), (self.controller_grid, cc)):
            for i in range(8):
                grid.columnconfigure(i, weight=1 if i < cols else 0,
                                     uniform="cards" if i < cols else "")
            for i, card in enumerate(grid.winfo_children()):
                card.grid(row=i // cols, column=i % cols, sticky="nsew", padx=gap, pady=gap)

    def _render_banner(self) -> None:
        for w in self.banner.winfo_children():
            w.destroy()
        items: List[Problem] = list(self.snap.problems)
        if self.demo:
            items.insert(0, Problem("Demo mode", "These are pretend devices. Nothing real is changed."))
        elif self.transport is None:
            items.insert(0, Problem("This computer isn't supported",
                                    "SC Hammer works on Linux (including SteamOS) and Windows. "
                                    "You can still try it with pretend devices.", "nolinux"))
        elif hasattr(os, "geteuid") and os.geteuid() == 0:
            items.insert(0, Problem("Running as root",
                                    "That isn't needed and can cause trouble. Start the app "
                                    "normally."))
        if self.update_version:
            items.append(Problem("SC Hammer %s is available" % self.update_version,
                                 "You have %s. Download the new version from GitHub." % __version__,
                                 "update"))
        f = self.fonts
        for p in items:
            calm = p.kind in ("info", "update")
            box = tk.Frame(self.banner, bg=C["banner"] if calm else C["warn_bg"],
                           padx=f.px(16), pady=f.px(10))
            box.pack(fill="x", padx=f.px(20), pady=(f.px(12), 0))
            fg = C["text"] if calm else C["warn"]
            tk.Label(box, text=p.title, bg=box.cget("bg"), fg=fg, font=f["bold"],
                     anchor="w").grid(row=0, column=0, sticky="w")
            auto_wrap(tk.Label(box, text=p.detail, bg=box.cget("bg"), fg=C["muted"],
                               font=f["small"], anchor="w", justify="left"),
                      f.px(400)).grid(row=1, column=0, sticky="ew")
            box.columnconfigure(0, weight=1)
            if p.kind == "permission":
                Button(box, f, "Fix permissions", self.on_permission_help, "secondary",
                       bg=box.cget("bg")).grid(row=0, column=1, rowspan=2)
            elif p.kind == "nolinux":
                Button(box, f, "Try the demo", self.switch_to_demo, "primary",
                       bg=box.cget("bg")).grid(row=0, column=1, rowspan=2)
            elif p.kind == "update":
                Button(box, f, "Download", lambda: webbrowser.open(updates.RELEASES_PAGE),
                       "primary", bg=box.cget("bg")).grid(row=0, column=1, rowspan=2)

    # ---------- action bar ----------

    def _render_actionbar(self) -> None:
        f = self.fonts
        bar = self.actionbar
        for w in bar.winfo_children():
            w.destroy()
        # Buttons are packed first so they always fit; the description wraps in what's left.
        right = tk.Frame(bar, bg=C["card"])
        right.pack(side="right")
        left = tk.Frame(bar, bg=C["card"])
        left.pack(side="left", fill="x", expand=True, padx=(0, f.px(12)))

        def line(text: str, color: str = "text", font: str = "bold") -> None:
            auto_wrap(tk.Label(left, text=text, bg=C["card"], fg=C[color], font=f[font],
                               anchor="w", justify="left"), f.px(400)).pack(fill="x")

        if self.pick:
            op, src = self.pick
            hint = dict((o, h) for o, _, h in ACTION_BUTTONS).get(op, "")
            line(hint + " Click one of the green slots.")
            line("Esc or Cancel to stop." if self.targets else
                 "There's no slot this can go to right now.", "muted", "small")
            Button(right, f, "Cancel", self.cancel, "secondary", bg=C["card"]).pack(side="right")
            return
        if not self.selected:
            line("Click a slot to see what you can do with it.")
            line("Tip: you can also drag a slot onto another slot, or right-click it.", "muted",
                 "small")
            return
        dev, slot = self.lookup(self.selected)
        where = (self.describer.n.receiver(dev.serial) if self.selected.kind == a.RECEIVER
                 else self.describer.n.controller(dev.serial))
        text = (self.describer.receiver_slot(dev, slot) if self.selected.kind == a.RECEIVER
                else self.describer.controller_slot(dev, slot))
        line("%s · %s:  %s" % (where, a.sn(dev, slot), text.main))
        line(text.sub, "muted", "small")
        if isinstance(dev, Controller) and not dev.usb:
            line("This controller is wireless. Plug it in with USB to change its slots.", "warn",
                 "small")
        any_button = False
        for op in (a.ERASE,):
            if self._can(op, self.selected, None):
                Button(right, f, "Erase", lambda: self.start(a.ERASE, self.selected), "danger",
                       bg=C["card"]).pack(side="right", padx=(f.px(8), 0))
                any_button = True
        for op, label, _ in reversed(ACTION_BUTTONS):
            if self._targets_for(op, self.selected):
                Button(right, f, label, lambda op=op: self.begin_pick(op), "secondary",
                       bg=C["card"]).pack(side="right", padx=(f.px(8), 0))
                any_button = True
        if not any_button:
            tk.Label(right, text="Nothing to do with this slot right now.", bg=C["card"],
                     fg=C["faint"], font=f["small"]).pack(side="right")

    def _can(self, op: str, src: a.SlotRef, dst: Optional[a.SlotRef]) -> bool:
        try:
            a.plan(self.snap, op, src, dst)
            return True
        except a.NotAllowed:
            return False

    def _targets_for(self, op: str, src: a.SlotRef) -> set:
        return {ref for ref in self.rows or self._all_refs() if ref != src and self._can(op, src, ref)}

    def _compute_targets(self) -> None:
        if self.pick:
            self.targets = {ref for ref in self._all_refs()
                            if ref != self.pick[1] and self._can(self.pick[0], self.pick[1], ref)}
        elif self.drag:
            src = self.drag["src"]
            self.targets = {ref for ref in self._all_refs()
                            if ref != src and a.options(self.snap, src, ref)}
        else:
            self.targets = set()

    def repaint(self) -> None:
        self._compute_targets()
        for row in self.rows.values():
            row.paint()
        self._render_actionbar()

    # ---------- interaction ----------

    def row_at(self, x_root: int, y_root: int) -> Optional[SlotRow]:
        w = self.root.winfo_containing(x_root, y_root)
        while w is not None and not isinstance(w, SlotRow):
            w = w.master
        return w

    def on_press(self, e: tk.Event) -> None:
        row = self.row_at(e.x_root, e.y_root)
        if row and not self.busy:
            self.press = {"ref": row.ref, "x": e.x_root, "y": e.y_root}

    def on_motion(self, e: tk.Event) -> None:
        if not self.press or self.busy or self.pick:
            return
        if not self.drag:
            if abs(e.x_root - self.press["x"]) + abs(e.y_root - self.press["y"]) < DRAG_THRESHOLD:
                return
            dev, slot = self.lookup(self.press["ref"])
            if slot.state != USED:
                return
            self._start_drag(self.press["ref"])
        self.drag["ghost"].geometry("+%d+%d" % (e.x_root + 14, e.y_root + 10))

    def _start_drag(self, ref: a.SlotRef) -> None:
        f = self.fonts
        ghost = tk.Toplevel(self.root)
        ghost.overrideredirect(True)
        try:
            ghost.attributes("-topmost", True)
        except tk.TclError:
            pass
        text = self.rows[ref].ref
        dev, slot = self.lookup(text)
        desc = (self.describer.receiver_slot(dev, slot) if ref.kind == a.RECEIVER
                else self.describer.controller_slot(dev, slot)).main
        tk.Label(ghost, text="  %s  " % desc, bg=C["accent"], fg=C["on_accent"], font=f["bold"],
                 padx=f.px(8), pady=f.px(5)).pack()
        self.drag = {"src": ref, "ghost": ghost}
        self.selected = ref
        self.repaint()

    def on_release(self, e: tk.Event) -> None:
        press, self.press = self.press, None
        if self.drag:
            src = self.drag["src"]
            self.drag["ghost"].destroy()
            self.drag = None
            row = self.row_at(e.x_root, e.y_root)
            self.repaint()
            if row and row.ref != src:
                self.drop(src, row.ref, e.x_root, e.y_root)
            return
        if press and not self.busy:
            row = self.row_at(e.x_root, e.y_root)
            if row and row.ref == press["ref"]:
                self.click(row.ref)

    def click(self, ref: a.SlotRef) -> None:
        if self.pick:
            op, src = self.pick
            if ref == src:
                return
            if ref not in self.targets:
                try:
                    a.plan(self.snap, op, src, ref)
                except a.NotAllowed as err:
                    self.say(str(err), "warn")
                return
            self.pick = None
            self.repaint()
            self.start(op, src, ref)
            return
        self.selected = None if self.selected == ref else ref
        self.repaint()

    def drop(self, src: a.SlotRef, dst: a.SlotRef, x: int, y: int) -> None:
        ops = a.options(self.snap, src, dst)
        if not ops:
            try:
                a.plan(self.snap, a.MOVE, src, dst)
            except a.NotAllowed as err:
                self.say(str(err), "warn")
            return
        if len(ops) == 1:
            self.start(ops[0], src, dst)
            return
        self._menu([(a.OP_LABELS[op], lambda op=op: self.start(op, src, dst)) for op in ops], x, y)

    def on_context(self, e: tk.Event) -> None:
        row = self.row_at(e.x_root, e.y_root)
        if not row or self.busy:
            return
        self.pick = None
        self.selected = row.ref
        self.repaint()
        items = [(label, lambda op=op: self.begin_pick(op))
                 for op, label, _ in ACTION_BUTTONS if self._targets_for(op, row.ref)]
        if self._can(a.ERASE, row.ref, None):
            items.append(("Erase", lambda: self.start(a.ERASE, row.ref)))
        if not items:
            items.append(("Nothing to do with this slot", None))
        self._menu(items, e.x_root, e.y_root)

    def _menu(self, items, x: int, y: int) -> None:
        f = self.fonts
        m = tk.Menu(self.root, tearoff=0, bg=C["card"], fg=C["text"], activebackground=C["selected"],
                    activeforeground=C["text"], font=f["base"], bd=0, relief="flat")
        for label, cmd in items:
            if cmd:
                m.add_command(label="  " + label + "  ", command=cmd)
            else:
                m.add_command(label="  " + label + "  ", state="disabled")
        m.add_separator()
        m.add_command(label="  Cancel  ", command=lambda: None)
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def begin_pick(self, op: str) -> None:
        if self.selected:
            self.pick = (op, self.selected)
            self.repaint()

    def cancel(self) -> None:
        if self.pick:
            self.pick = None
        else:
            self.selected = None
        self.repaint()

    # ---------- running actions ----------

    def names(self) -> Names:
        return Names(self.store, self.snap)

    def start(self, op: str, src: a.SlotRef, dst: Optional[a.SlotRef] = None) -> None:
        if self.busy:
            return
        try:
            pl = a.plan(self.snap, op, src, dst, self.names())
        except a.NotAllowed as err:
            self.say(str(err), "warn")
            return
        if not self.dialog(dialogs.confirm, pl):
            return
        self.run_plan(pl)

    def run_plan(self, pl: a.Plan) -> None:
        snap = self.snap

        def job(progress):
            self.store.save_backup(snap, pl.title.rstrip("?"))
            a.execute(snap, pl, progress)
            return self.scanner.scan()

        def done(new_snap: Snapshot) -> None:
            self.set_busy(False)
            self.snap = new_snap
            self.render()
            self.say(("Done. " + pl.after).strip(), "ok")
            if pl.after:
                self.dialog(dialogs.message, "Done", pl.after)

        def fail(err: Exception) -> None:
            self.set_busy(False)
            self.dialog(dialogs.message, "That didn't work", str(err) or err.__class__.__name__,
                        "error", ["A backup was saved before the change. If some slots look wrong, "
                                  "open Backups and restore the newest one."])
            self.refresh()

        self.set_busy(True, "Working…")
        self.worker.submit(job, done, fail)

    def restart_controller(self, serial: str) -> None:
        self._controller_command(lambda: a.plan_restart(self.snap, serial, self.names()),
                                 "Restarted.")

    def turn_off_controller(self, serial: str) -> None:
        self._controller_command(lambda: a.plan_turn_off(self.snap, serial, self.names()),
                                 "Disconnected.")

    def go_wireless(self, serial: str) -> None:
        c = self.snap.controller(serial)
        if self.busy or not c or not c.usb:
            return
        choices = []
        for s in c.slots:
            t = self.describer.controller_slot(c, s)
            label = "%s  (%s):  %s" % (slot_name(True, s.index).capitalize(),
                                       CONTROLLER_SLOT_COMBOS[s.index],
                                       t.main if s.state == USED else "Empty")
            if s.active:
                label += "   (in use now)"
            choices.append((label, s.state == USED))
        if not any(ok for _, ok in choices):
            self.say("This controller has no pairings, so it has nothing to connect to. Pair it "
                     "first.", "warn")
            return
        default = c.active if c.active is not None and choices[c.active][1] else \
            next(i for i, (_, ok) in enumerate(choices) if ok)
        slot = self.dialog(dialogs.choose_slot, "Go wireless",
                           "The controller restarts in wireless mode and connects to the receiver "
                           "in the slot you pick. You can unplug its USB cable afterwards.",
                           choices, default, "Restart wireless")
        if slot is None:
            return
        self._controller_command(lambda: a.plan_go_wireless(self.snap, serial, slot, self.names()),
                                 "Restarted in wireless mode.", confirm=False)

    def _controller_command(self, make_plan, done_text: str, confirm: bool = True) -> None:
        """Restart / turn off / go wireless: no slot changes, so no backup; refresh once the
        controller has gone."""
        if self.busy:
            return
        try:
            pl = make_plan()
        except a.NotAllowed as err:
            self.say(str(err), "warn")
            return
        if confirm and not self.dialog(dialogs.confirm, pl):
            return
        snap = self.snap

        def job(progress):
            a.execute(snap, pl, progress)

        def done(_):
            self.set_busy(False)
            self.say(done_text, "ok")
            self.root.after(2500, self.refresh)

        def fail(err: Exception) -> None:
            self.set_busy(False)
            self.dialog(dialogs.message, "That didn't work", str(err) or err.__class__.__name__,
                        "error")
            self.refresh()

        self.set_busy(True, "Working…")
        self.worker.submit(job, done, fail)

    def refresh(self) -> None:
        if not self.scanner or self.busy:
            return
        self.set_busy(True, "Looking for receivers and controllers…")

        def done(snap: Snapshot) -> None:
            self.set_busy(False)
            self.snap = snap
            self.render()
            n_r, n_c = len(snap.receivers), len(snap.controllers)
            self.say("Found %d receiver%s and %d controller%s." %
                     (n_r, "" if n_r == 1 else "s", n_c, "" if n_c == 1 else "s"))

        def fail(err: Exception) -> None:
            self.set_busy(False)
            self.say("Couldn't read the devices: %s" % err, "error")

        self.worker.submit(lambda progress: self.scanner.scan(), done, fail)

    def set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.root.configure(cursor="watch" if busy else "")
        if busy:
            self.say(text, "muted", sticky=True)

    def say(self, text: str, kind: str = "muted", sticky: bool = False) -> None:
        color = {"ok": C["ok"], "warn": C["warn"], "error": C["danger"]}.get(kind, C["muted"])
        self.status.configure(text=text, fg=color)
        if self._status_job:
            self.root.after_cancel(self._status_job)
            self._status_job = None
        if not sticky:
            self._status_job = self.root.after(10000, lambda: self.status.configure(text=""))

    def _poll_worker(self) -> None:
        try:
            while True:
                cb, value = self.worker.results.get_nowait()
                if cb == "progress":
                    self.say(value, "muted", sticky=True)
                else:
                    cb(value)
        except queue.Empty:
            pass
        self.root.after(40, self._poll_worker)

    def _check_for_update(self) -> None:
        # Its own thread, not the Worker: a slow network must never hold up device jobs. The result
        # goes through the Worker's results queue so it's handled on the UI thread.
        v = updates.newer_release(__version__)
        if v:
            self.worker.results.put((self._on_update, v))

    def _on_update(self, version: str) -> None:
        self.update_version = version
        self._render_banner()

    def _watch_hotplug(self) -> None:
        try:
            sig = self.transport.signature()
        except OSError:
            sig = None
        if sig != self.signature and not self.busy and not self.modal and not self.drag:
            first = self.signature is None
            self.signature = sig
            if not first:
                self.root.after(700, self.refresh)  # let a new device finish enumerating
        self.root.after(1500, self._watch_hotplug)

    def dialog(self, fn, *args):
        self.modal = True
        try:
            return fn(self.root, self.fonts, *args)
        finally:
            self.modal = False

    # ---------- header actions ----------

    def rename(self, serial: str) -> None:
        current = self.store.name(serial)
        is_receiver = self.snap.receiver(serial) is not None
        what = "receiver" if is_receiver else "controller"
        name = self.dialog(dialogs.ask_text, "Rename %s" % what,
                           "Give this %s a name you'll recognise, like \"Living room\" or "
                           "\"Sam's controller\". It's only stored on this computer. Leave it "
                           "empty to go back to the default." % what, current)
        if name is None:
            return
        try:
            self.store.set_name(serial, name[:40])
        except OSError as err:
            self.say("Couldn't save the name: %s" % err, "error")
        self.render()

    def on_backups(self) -> None:
        if self.busy:
            return
        backup = self.dialog(dialogs.choose_backup, self.store.backups(), self.store.backup_dir)
        if not backup:
            return
        try:
            pl = a.plan_restore(self.snap, backup, self.names())
        except a.NotAllowed as err:
            self.dialog(dialogs.message, "Nothing to restore", str(err))
            return
        if self.dialog(dialogs.confirm, pl):
            self.run_plan(pl)

    def on_help(self) -> None:
        self.dialog(dialogs.show_help)

    def on_permission_help(self) -> None:
        self.dialog(dialogs.show_permission_help, self.copy_text)

    def copy_text(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.say("Copied to the clipboard.", "ok")

    def toggle_keys(self) -> None:
        self.show_keys = not self.show_keys
        self.keys_btn.configure(text="Hide keys" if self.show_keys else "Show keys")
        self.render()

    def zoom(self, factor: Optional[float]) -> None:
        self.fonts.zoom = 1.0 if factor is None else min(2.5, max(0.6, self.fonts.zoom * factor))
        self.fonts.apply()
        self.render()

    def switch_to_demo(self) -> None:
        from ..fake import FakeTransport, demo_world
        self.demo = True
        self.transport = FakeTransport(demo_world())
        self.scanner = Scanner(self.transport)
        self.store = demo_store()
        self.refresh()
        self.root.after(1500, self._watch_hotplug)


def demo_store() -> Store:
    d = tempfile.mkdtemp(prefix="sc-hammer-demo-")
    return Store(os.path.join(d, "config"), os.path.join(d, "data"))


def desktop_scale() -> float:
    """The desktop's UI scale: Windows' display scaling, or Xft.dpi on Linux (KDE/GNOME set it
    from the display scaling); else 1. Call before creating the Tk window."""
    if sys.platform == "win32":
        import ctypes
        try:
            # Draw at the real resolution instead of letting Windows blow up a blurry bitmap.
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        try:
            return min(3.0, max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96))
        except (AttributeError, OSError):
            return 1.0
    if not sys.platform.startswith("linux"):
        return 1.0
    try:
        out = subprocess.run(["xrdb", "-query"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return 1.0
    for line in out.splitlines():
        if line.startswith("Xft.dpi:"):
            try:
                return min(3.0, max(1.0, float(line.split(":", 1)[1]) / 96))
            except ValueError:
                break
    return 1.0


def run(transport, demo: bool = False, zoom: Optional[float] = None) -> None:
    scale = desktop_scale()
    root = tk.Tk(className="sc-hammer")
    root.title("%s %s%s" % (APP_NAME, __version__, " (demo)" if demo else ""))
    if sys.platform.startswith("linux") or sys.platform == "win32":
        # Keep Tk's own point-sized defaults (menus, dialogs) at the desktop's scale too.
        root.tk.call("tk", "scaling", 96 * scale / 72)
    if zoom is None:
        # A big screen at 100% scaling (a TV on a Steam Machine) gets bigger text.
        zoom = 1.25 if scale == 1.0 and root.winfo_screenheight() >= 1400 else 1.0
    try:
        root.iconphoto(True, tk.PhotoImage(file=os.path.join(ASSETS, "icon-256.png")))
    except tk.TclError:
        pass
    w = min(1280, root.winfo_screenwidth())
    h = min(820, root.winfo_screenheight() - 60)
    root.geometry("%dx%d" % (w, h))
    root.minsize(560, 420)
    App(root, transport, demo_store() if demo else Store(), demo, zoom, scale)
    root.mainloop()
