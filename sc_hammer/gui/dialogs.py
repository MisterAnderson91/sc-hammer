"""Modal dialogs in the app's own style."""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from typing import Callable, List, Optional, Tuple

from ..actions import Plan
from .theme import C, Fonts
from .widgets import Button

UDEV_RULE = 'KERNEL=="hidraw*", ATTRS{idVendor}=="28de", MODE="0660", TAG+="uaccess"'


class Dialog(tk.Toplevel):
    """Centered over the parent, grabs input until closed. self.result is what show() returns."""

    def __init__(self, parent: tk.Misc, fonts: Fonts, title: str, width: int = 520):
        super().__init__(parent, bg=C["card"])
        self.withdraw()
        self.fonts = fonts
        self.result = None
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.body = tk.Frame(self, bg=C["card"], padx=fonts.px(24), pady=fonts.px(20))
        self.body.pack(fill="both", expand=True)
        self.wrap = fonts.px(width)
        self.buttons = tk.Frame(self, bg=C["card"], padx=fonts.px(24), pady=fonts.px(16))
        self.buttons.pack(fill="x")
        self.bind("<Escape>", lambda e: self.close(None))
        self.protocol("WM_DELETE_WINDOW", lambda: self.close(None))

    def heading(self, text: str, color: str = "text") -> None:
        tk.Label(self.body, text=text, bg=C["card"], fg=C[color], font=self.fonts["big"],
                 wraplength=self.wrap, justify="left", anchor="w").pack(fill="x", pady=(0, self.fonts.px(10)))

    def para(self, text: str, color: str = "text", font: str = "base", parent=None) -> tk.Label:
        lab = tk.Label(parent or self.body, text=text, bg=C["card"], fg=C[color],
                       font=self.fonts[font], wraplength=self.wrap, justify="left", anchor="w")
        lab.pack(fill="x", pady=(0, self.fonts.px(6)))
        return lab

    def warning_box(self, lines: List[str]) -> None:
        box = tk.Frame(self.body, bg=C["warn_bg"], padx=self.fonts.px(12), pady=self.fonts.px(8))
        box.pack(fill="x", pady=(self.fonts.px(6), self.fonts.px(6)))
        for line in lines:
            tk.Label(box, text="⚠  " + line, bg=C["warn_bg"], fg=C["warn"], font=self.fonts["base"],
                     wraplength=self.wrap - self.fonts.px(24), justify="left",
                     anchor="w").pack(fill="x", pady=self.fonts.px(2))

    def add_button(self, text: str, value, kind: str = "secondary") -> Button:
        b = Button(self.buttons, self.fonts, text, lambda: self.close(value), kind, bg=C["card"])
        b.pack(side="right", padx=(self.fonts.px(8), 0))
        return b

    def close(self, value) -> None:
        self.result = value
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self):
        self.update_idletasks()
        parent = self.master.winfo_toplevel()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()
        self.wait_window()
        return self.result


def confirm(parent: tk.Misc, fonts: Fonts, pl: Plan) -> bool:
    d = Dialog(parent, fonts, pl.verb)
    d.heading(pl.title)
    for line in pl.lines:
        d.para(line)
    if pl.warnings:
        d.warning_box(pl.warnings)
    if pl.after:
        d.para(pl.after, "muted")
    if pl.backup:
        d.para("A backup of every slot is saved first, so you can undo this from Backups.",
               "faint", "small")
    kind = "danger" if pl.verb == "Erase" else "primary"
    go = d.add_button(pl.verb, True, kind)
    d.add_button("Cancel", False)
    d.bind("<Return>", lambda e: d.close(True))
    go.focus_set()
    return bool(d.show())


def message(parent: tk.Misc, fonts: Fonts, title: str, text: str, kind: str = "info",
            extra: Optional[List[str]] = None) -> None:
    d = Dialog(parent, fonts, title)
    d.heading(title, {"error": "danger", "warn": "warn"}.get(kind, "text"))
    d.para(text)
    for line in extra or []:
        d.para(line, "muted")
    d.add_button("OK", None, "primary")
    d.bind("<Return>", lambda e: d.close(None))
    d.show()


def ask_text(parent: tk.Misc, fonts: Fonts, title: str, prompt: str, initial: str) -> Optional[str]:
    d = Dialog(parent, fonts, title, width=420)
    d.heading(title)
    d.para(prompt, "muted")
    var = tk.StringVar(value=initial)
    entry = tk.Entry(d.body, textvariable=var, font=fonts["base"], bg=C["slot"], fg=C["text"],
                     insertbackground=C["text"], relief="flat", highlightthickness=fonts.px(2),
                     highlightcolor=C["accent"], highlightbackground=C["card_line"])
    entry.pack(fill="x", ipady=fonts.px(6), pady=(fonts.px(4), 0))
    entry.select_range(0, "end")
    d.add_button("Save", "save", "primary")
    d.add_button("Cancel", None)
    d.bind("<Return>", lambda e: d.close("save"))
    d.after(50, entry.focus_set)
    return var.get() if d.show() == "save" else None


def choose_slot(parent: tk.Misc, fonts: Fonts, title: str, intro: str,
                choices: List[Tuple[str, bool]], default: int, go: str) -> Optional[int]:
    """Pick one of a few options (radio buttons); disabled ones are shown but can't be picked."""
    d = Dialog(parent, fonts, title, width=480)
    d.heading(title)
    d.para(intro, "muted")
    var = tk.IntVar(value=default)
    for i, (label, enabled) in enumerate(choices):
        tk.Radiobutton(d.body, text=label, variable=var, value=i, font=fonts["base"],
                       bg=C["card"], fg=C["text"] if enabled else C["faint"],
                       activebackground=C["card"], activeforeground=C["text"],
                       selectcolor=C["slot"], disabledforeground=C["faint"],
                       highlightthickness=0, anchor="w", justify="left",
                       wraplength=d.wrap - fonts.px(30),
                       state="normal" if enabled else "disabled").pack(fill="x", pady=fonts.px(3))
    d.add_button(go, "go", "primary")
    d.add_button("Cancel", None)
    d.bind("<Return>", lambda e: d.close("go"))
    return var.get() if d.show() == "go" else None


def open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def choose_backup(parent: tk.Misc, fonts: Fonts, backups: List[dict], folder: str) -> Optional[dict]:
    d = Dialog(parent, fonts, "Backups", width=560)
    d.heading("Backups")
    d.para("A backup of every slot is saved automatically before each change. Pick one to put "
           "the slots back the way they were then.", "muted")
    if not backups:
        d.para("There are no backups yet. One is made the first time you change something.")
    else:
        frame = tk.Frame(d.body, bg=C["card"])
        frame.pack(fill="both", expand=True, pady=(fonts.px(6), 0))
        lb = tk.Listbox(frame, font=fonts["base"], bg=C["slot"], fg=C["text"], height=10,
                        selectbackground=C["selected"], selectforeground=C["text"],
                        relief="flat", highlightthickness=0, activestyle="none",
                        width=60)
        sb = tk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for b in backups:
            lb.insert("end", "  %s   —   before: %s" % (b["created_display"], b.get("reason", "")))
        lb.selection_set(0)

        def restore():
            sel = lb.curselection()
            if sel:
                d.close(backups[sel[0]])

        lb.bind("<Double-Button-1>", lambda e: restore())
        b = Button(d.buttons, fonts, "Restore…", restore, "primary", bg=C["card"])
        b.pack(side="right", padx=(fonts.px(8), 0))
    d.add_button("Close", None)
    Button(d.buttons, fonts, "Open backup folder", lambda: open_folder(folder), "ghost",
           bg=C["card"]).pack(side="left")
    return d.show()


HELP: List[Tuple[str, str]] = [
    ("What this app does",
     "Steam Controllers talk to a receiver: a Steam Controller Puck, an OpenPuck, or the receiver "
     "built into a Steam Machine. Each receiver remembers up to 4 controllers, and each "
     "controller remembers up to 2 receivers. Every remembered pairing lives in a numbered slot. "
     "This app lets you see those slots and rearrange them."),
    ("Reading the screen",
     "Matching coloured dots mean two slots belong to the same pairing: the receiver slot and the "
     "controller slot that work together. \"Connected\" means the controller is talking to the "
     "receiver right now. \"In use\" marks the slot a controller is currently using.\n"
     "A controller's two slots are its right and left slots, like in Steam. Turning it on with "
     "A + R1 + Steam uses the right slot, A + L1 + Steam the left one."),
    ("Changing things",
     "Click a slot, then pick an action at the bottom of the window:\n"
     "•  Move: put the pairing in an empty slot. Moving it to another receiver or controller "
     "pairs it afresh with the other side (which has to be plugged in) and clears the old slot.\n"
     "•  Swap: trade places with another slot on the same receiver or controller.\n"
     "•  Pair: make a brand new pairing between a receiver slot and a controller plugged in with "
     "USB. The controller's old pairing in that slot is removed from both sides.\n"
     "•  Erase: forget a pairing, on both sides.\n"
     "A controller slot only ever pairs with one receiver slot, so nothing connects twice.\n"
     "You can also drag a slot onto another slot, or right-click a slot."),
    ("Controllers",
     "A controller connected wirelessly is shown so you can see its slots, but to change them "
     "it has to be plugged in with a USB cable. After a controller's slots change, restart it "
     "(the Restart button on its card, or unplug it and turn it off and on). \"Go wireless\" on a "
     "USB controller's card restarts it in wireless mode, using the slot you pick. "
     "\"Disconnect wireless\" on a wireless controller's card drops its connection (which turns it "
     "off unless it's plugged in with USB)."),
    ("Undo",
     "Before every change, a backup of all slots is saved. Open Backups to put things back."),
    ("Keyboard",
     "F5 refresh  ·  Esc cancel  ·  Delete erase the selected slot  ·  Ctrl + / Ctrl − text size"),
]


def show_help(parent: tk.Misc, fonts: Fonts) -> None:
    d = Dialog(parent, fonts, "Help", width=600)
    d.heading("How it works")
    for title, text in HELP:
        tk.Label(d.body, text=title, bg=C["card"], fg=C["accent"], font=fonts["h2"],
                 anchor="w").pack(fill="x", pady=(fonts.px(8), fonts.px(2)))
        d.para(text)
    d.add_button("Close", None, "primary")
    d.show()


def show_permission_help(parent: tk.Misc, fonts: Fonts, on_copy: Callable[[str], None]) -> None:
    d = Dialog(parent, fonts, "Fix permissions", width=640)
    d.heading("Let this account use the receivers")
    d.para("On SteamOS this normally works out of the box, because Steam's own device rules let "
           "the logged-in user talk to Valve devices. If it doesn't, run these commands once in a "
           "terminal (Konsole). They add the same rule Steam uses:")
    cmds = ("echo '%s' | sudo tee /etc/udev/rules.d/70-sc-hammer.rules\n"
            "sudo udevadm control --reload-rules\n"
            "sudo udevadm trigger") % UDEV_RULE
    box = tk.Text(d.body, height=4, font=fonts["mono"], bg=C["slot"], fg=C["text"],
                  relief="flat", wrap="none", padx=fonts.px(10), pady=fonts.px(8),
                  highlightthickness=0)
    box.insert("1.0", cmds)
    box.configure(state="disabled")
    box.pack(fill="x", pady=fonts.px(6))
    d.para("Then unplug the receiver or controller and plug it back in, and click Refresh. "
           "On SteamOS you need a password for sudo; set one with the passwd command if you "
           "haven't already. Don't start this app itself with sudo.", "muted")
    Button(d.buttons, fonts, "Copy commands", lambda: on_copy(cmds), "secondary",
           bg=C["card"]).pack(side="left")
    d.add_button("Close", None, "primary")
    d.show()
