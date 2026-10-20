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
    return None


def check() -> int:
    """Report whether this user can open every Valve receiver/controller without sudo."""
    if not sys.platform.startswith("linux"):
        print("--check only works on Linux.")
        return 2
    from .hidraw import LinuxTransport
    print("Running as %s (uid %d)" % (os.environ.get("USER", "?"), os.geteuid()))
    devices = [d for d in LinuxTransport().enumerate()
               if d.pid in RECEIVER_PIDS or d.pid in CONTROLLER_PIDS]
    if not devices:
        print("No Valve receivers or controllers are plugged in. Plug one in and run this again.")
        return 1
    bad = 0
    for dev in devices:
        print("\n%s (28de:%04x) serial %s" % (dev.product or "?", dev.pid, dev.serial or "?"))
        for info in dev.command_ifaces():
            st = os.stat(info.node)
            try:
                os.close(os.open(info.node, os.O_RDWR))
                ok = "OK, can open"
            except PermissionError:
                ok = "PERMISSION DENIED"
                bad += 1
            except OSError as e:
                ok = "error: %s" % e
                bad += 1
            print("  %-14s mode %o  %s" % (info.node, st.st_mode & 0o777, ok))
    print()
    if bad:
        print("Some devices can't be opened by this user. See \"Permissions\" in the README.")
        return 1
    print("All good: everything can be opened without sudo.")
    return 0


def list_devices(transport) -> int:
    if transport is None:
        print("Listing devices only works on Linux (or with --demo).")
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sc-hammer",
                                 description="%s: move, swap, copy and erase Steam Controller "
                                             "pairings." % APP_NAME)
    ap.add_argument("--demo", action="store_true",
                    help="use pretend devices; nothing real is changed")
    ap.add_argument("--list", action="store_true", help="print all slots and exit")
    ap.add_argument("--check", action="store_true",
                    help="check this user can open the devices (no sudo needed) and exit")
    ap.add_argument("--zoom", type=float, default=None, help="text size, e.g. 1.5")
    ap.add_argument("--version", action="version", version="%s %s" % (APP_NAME, __version__))
    args = ap.parse_args(argv)
    if args.check:
        return check()
    transport = make_transport(args.demo)
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
