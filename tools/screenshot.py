#!/usr/bin/env python3
"""Opens the demo, puts it in a few states and screenshots the window (macOS `screencapture`
or Linux `import`). Used for the README images and for checking layout changes.

    python3 tools/screenshot.py OUTDIR
"""
import os
import subprocess
import sys
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sc_hammer import actions as a  # noqa: E402
from sc_hammer.fake import FakeTransport, demo_world  # noqa: E402
from sc_hammer.gui.app import App, demo_store  # noqa: E402


def grab(root, path):
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    if sys.platform == "darwin":
        subprocess.run(["screencapture", "-x", "-R%d,%d,%d,%d" % (x, y, w, h), path], check=False)
    else:
        subprocess.run(["import", "-window", str(root.winfo_id()), path], check=False)
    print("wrote", path)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    root = tk.Tk(className="sc-hammer")
    root.geometry("1280x800+40+40")
    app = App(root, FakeTransport(demo_world()), demo_store(), True, 1.0)
    living = app.snap.receivers[0].serial if app.snap.receivers else None

    def step(n=0):
        if app.busy or living is None:
            root.after(200, step, n)
            return
        if n == 0:
            grab(root, os.path.join(out, "main.png"))
            app.selected = a.SlotRef(a.RECEIVER, app.snap.receivers[0].serial, 2)
            app.repaint()
        elif n == 1:
            grab(root, os.path.join(out, "selected.png"))
            app.begin_pick(a.MOVE)
        elif n == 2:
            grab(root, os.path.join(out, "pick.png"))
            app.cancel()
            app.cancel()
            app.scroll.canvas.yview_moveto(1.0)
        elif n == 3:
            grab(root, os.path.join(out, "controllers.png"))
            root.destroy()
            return
        root.after(600, step, n + 1)

    def wait_scan():
        nonlocal living
        if app.busy or not app.snap.receivers:
            root.after(200, wait_scan)
            return
        living = app.snap.receivers[0].serial
        root.after(500, step)

    root.after(300, wait_scan)
    root.mainloop()


if __name__ == "__main__":
    main()
