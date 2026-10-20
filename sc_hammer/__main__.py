"""Command line entry point. With no options it opens the window."""
from __future__ import annotations

import argparse
import os
import sys

from . import APP_NAME, __version__
from . import protocol as p
from .model import CONTROLLER_PIDS, RECEIVER_PIDS, Scanner, slot_name


def make_transport(demo: bool):
    if demo:
        from .fake import FakeTransport, demo_world
        return FakeTransport(demo_world())
    if sys.platform.startswith("linux"):
        from .hidraw import LinuxTransport
        return LinuxTransport()
    if sys.platform == "win32":
        from .winhid import WindowsTransport
        return WindowsTransport()
    return None


def check() -> int:
    """Report whether this user can open every Valve receiver/controller (no sudo/admin)."""
    transport = make_transport(False)
    if transport is None:
        print("--check only works on Linux and Windows.")
        return 2
    linux = sys.platform.startswith("linux")
    if linux:
        print("Running as %s (uid %d)" % (os.environ.get("USER", "?"), os.geteuid()))
    devices = [d for d in transport.enumerate()
               if d.pid in RECEIVER_PIDS or d.pid in CONTROLLER_PIDS]
    if not devices:
        print("No Valve receivers or controllers are plugged in. Plug one in and run this again.")
        return 1
    bad = 0
    for dev in devices:
        print("\n%s (28de:%04x) serial %s" % (dev.product or "?", dev.pid, dev.serial or "?"))
        for info in dev.command_ifaces():
            try:
                transport.open(info.node).close()
                ok = "OK, can open"
            except PermissionError:
                ok = "PERMISSION DENIED"
                bad += 1
            except OSError as e:
                ok = "error: %s" % e
                bad += 1
            if linux:
                mode = "mode %o" % (os.stat(info.node).st_mode & 0o777)
                print("  %-14s %s  %s" % (info.node, mode, ok))
            else:
                print("  interface %d  %s" % (info.number, ok))
    print()
    if bad:
        print("Some devices can't be opened by this user. See \"Permissions\" in the README.")
        return 1
    print("All good: everything can be opened without %s." % ("sudo" if linux else "admin"))
    return 0


def list_devices(transport) -> int:
    if transport is None:
        print("Listing devices only works on Linux and Windows (or with --demo).")
        return 2
    snap = Scanner(transport).scan()
    for prob in snap.problems:
        print("! %s: %s" % (prob.title, prob.detail))
    for r in snap.receivers:
        print("%s %s  %s" % (r.kind, r.serial, r.firmware))
        for s in r.slots:
            if s.state == "used":
                extra = "  connected" if s.connected else ""
                print("  slot %d: %s  key %s%s" % (s.index + 1, s.name, s.key.hex(), extra))
            else:
                print("  slot %d: %s" % (s.index + 1, s.state))
    for c in snap.controllers:
        how = "USB" if c.usb else "wireless via %s slot %d" % (c.via[0], c.via[1] + 1)
        print("Controller %s (%s)  %s" % (c.serial, how, c.firmware))
        for s in c.slots:
            mark = "  in use" if s.active else ""
            if s.state == "used":
                print("  %s: %s  key %s%s" % (slot_name(True, s.index), s.name, s.key.hex(), mark))
            else:
                print("  %s: %s%s" % (slot_name(True, s.index), s.state, mark))
    if not snap.receivers and not snap.controllers:
        print("No receivers or controllers found.")
    return 0


def _console_for_windowed_exe() -> None:
    """The Windows .exe is built without a console window, so print() goes nowhere (PyInstaller
    may hand Python a stand-in stdout rather than None). For --list/--check/--diagnose/--version,
    write to the terminal it was started from, or to a redirect."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import ctypes
    import msvcrt
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE: set when output is redirected
    # GetFileType: 0 = unknown (no real handle), 2 = a console, 1/3 = a file or pipe.
    if handle in (0, -1, None) or kernel32.GetFileType(handle) in (0, 2):
        if not kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return
        sys.stdout = sys.stderr = open("CONOUT$", "w", buffering=1)
        print()
        return
    fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
    sys.stdout = sys.stderr = open(fd, "w", buffering=1)


def diagnose(transport) -> int:
    """Everything about the Valve devices this computer sees, for bug reports. Printed, and also
    written to sc-hammer-diagnose.txt (in case the terminal can't show it)."""
    import platform
    lines = ["%s %s on %s (Python %s)" % (APP_NAME, __version__, platform.platform(),
                                          platform.python_version()), ""]
    if sys.platform == "win32":
        from .winhid import diagnose as raw
        lines += ["Raw HID collections:"] + raw()
    elif transport is not None:
        lines.append("Valve USB devices:")
        for dev in transport.enumerate():
            lines.append("28de:%04x bcd %04x serial %r product %r" %
                         (dev.pid, dev.bcd, dev.serial, dev.product))
            for i in sorted(dev.ifaces, key=lambda i: i.number):
                lines.append("  interface %d  usage page %s  class %s  %s  %r" % (
                    i.number, "%04x" % i.usage_page if i.usage_page is not None else "?",
                    i.hid_class, i.node, i.name))
    lines.append("")
    if transport is not None:
        snap = Scanner(transport).scan()
        lines.append("Found %d receiver(s), %d controller(s)." %
                     (len(snap.receivers), len(snap.controllers)))
        for prob in snap.problems:
            lines.append("! %s: %s" % (prob.title, prob.detail))
    text = "\n".join(lines) + "\n"
    print(text)
    for folder in (os.getcwd(), os.path.expanduser("~")):
        path = os.path.join(folder, "sc-hammer-diagnose.txt")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            print("Saved to %s" % path)
            break
        except OSError:
            continue
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sc-hammer",
                                 description="%s: move, swap, copy and erase Steam Controller "
                                             "pairings." % APP_NAME)
    ap.add_argument("--demo", action="store_true",
                    help="use pretend devices; nothing real is changed")
    ap.add_argument("--list", action="store_true", help="print all slots and exit")
    ap.add_argument("--check", action="store_true",
                    help="check this user can open the devices (no sudo needed) and exit")
    ap.add_argument("--diagnose", action="store_true",
                    help="describe every Valve device found (for bug reports) and exit")
    ap.add_argument("--zoom", type=float, default=None, help="text size, e.g. 1.5")
    ap.add_argument("--version", action="version", version="%s %s" % (APP_NAME, __version__))
    if set(sys.argv[1:] if argv is None else argv) & {"--list", "--check", "--diagnose",
                                                       "--version", "-h", "--help"}:
        _console_for_windowed_exe()
    args = ap.parse_args(argv)
    if args.check:
        return check()
    transport = make_transport(args.demo)
    if args.diagnose:
        return diagnose(transport)
    if args.list:
        return list_devices(transport)
    try:
        from .gui.app import run
    except ImportError as e:
        print("The window can't open: %s" % e, file=sys.stderr)
        return 1
    run(transport, demo=args.demo, zoom=args.zoom)
    return 0


if __name__ == "__main__":
    sys.exit(main())
