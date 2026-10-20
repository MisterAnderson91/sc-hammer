"""Plain-language text for cards and slots."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .actions import Namer
from .model import UNKNOWN, USED, slot_name, Controller, ControllerSlot, Receiver, ReceiverSlot, Snapshot
from .store import Store

SHORT_KIND = {
    "Steam Controller Puck": "Puck",
    "Steam Machine receiver": "Steam Machine",
    "OpenPuck": "OpenPuck",
    "OpenPuck (Steam Machine mode)": "OpenPuck",
}


def short_serial(serial: str) -> str:
    return serial[-5:] if len(serial) > 5 else serial


class Names(Namer):
    def __init__(self, store: Store, snap: Optional[Snapshot] = None):
        self.store = store
        self.snap = snap or Snapshot()

    def receiver(self, serial: str) -> str:
        nick = self.store.name(serial)
        if nick:
            return nick
        r = self.snap.receiver(serial)
        kind = SHORT_KIND.get(r.kind, "Receiver") if r else "Receiver"
        return "%s %s" % (kind, short_serial(serial))

    def controller(self, serial: str) -> str:
        nick = self.store.name(serial)
        if nick:
            return nick
        return "Controller %s" % short_serial(serial) if serial else "Unnamed controller"


@dataclass
class SlotText:
    main: str
    sub: str
    chips: List[Tuple[str, str]] = field(default_factory=list)  # (text, "ok"/"accent"/"warn")
    key: bytes = b""
    state: str = USED


class Describer:
    def __init__(self, snap: Snapshot, names: Names):
        self.snap = snap
        self.n = names
        self.colors: Dict[bytes, int] = {}
        for r in snap.receivers:
            for s in r.slots:
                self._color(s.key)
        for c in snap.controllers:
            for s in c.slots:
                self._color(s.key)

    def _color(self, key: bytes) -> None:
        if key and key not in self.colors:
            self.colors[key] = len(self.colors)

    def color_index(self, key: bytes) -> Optional[int]:
        return self.colors.get(key)

    def receiver_slot(self, r: Receiver, s: ReceiverSlot) -> SlotText:
        if s.state == UNKNOWN:
            return SlotText("Couldn't read this slot", "Click Refresh to try again.", state=UNKNOWN)
        if s.state != USED:
            return SlotText("Empty", "Free for a controller", state=s.state)
        t = SlotText(self.n.controller(s.name), "", key=s.key)
        dups = [(r2, s2) for r2, s2 in self.snap.receivers_with_key(s.key) if s2 is not s]
        if dups:
            # Left over from copying a pairing (older tools allowed it). Show it so it gets fixed.
            t.chips.append(("Duplicate", "warn"))
            t.sub = "Same pairing as %s slot %d. Erase one, or pair again." % (
                self.n.receiver(dups[0][0].serial), dups[0][1].index + 1)
            if s.connected:
                t.chips.append(("Connected", "ok"))
            return t
        if s.connected:
            t.chips.append(("Connected", "ok"))
            t.sub = "Connected right now"
            return t
        hits = [(c, cs) for c, cs in self.snap.controllers_with_key(s.key)]
        if not hits:
            t.sub = "Off, out of range, or on its other slot"
            return t
        c, cs = hits[0]
        if c.usb:
            t.sub = "Plugged in with USB (its %s)" % slot_name(True, cs.index)
        elif c.via and c.via[0] != r.serial:
            t.sub = "Connected to %s right now" % self.n.receiver(c.via[0])
        else:
            t.sub = "On, but not using this slot"
        return t

    def controller_slot(self, c: Controller, s: ControllerSlot) -> SlotText:
        if s.state == UNKNOWN:
            return SlotText("Couldn't read this slot", "Click Refresh to try again.", state=UNKNOWN)
        chips = [("In use", "accent")] if s.active else []
        if s.state != USED:
            return SlotText("Empty", "Free for a receiver", chips, state=s.state)
        hits = self.snap.receivers_with_key(s.key)
        if not hits:
            return SlotText(self.n.receiver(s.name), "That receiver isn't plugged in", chips, s.key)
        main = self.n.receiver(hits[0][0].serial)
        if len(hits) > 1:
            main += " + %d more" % (len(hits) - 1)
        if len(hits) == 1:
            sub = "Receiver slot %d" % (hits[0][1].index + 1)
        else:
            sub = "Works with " + ", ".join("%s (slot %d)" % (self.n.receiver(r.serial), rs.index + 1)
                                            for r, rs in hits)
        if len(hits) > 1 or any(o is not s and o.key == s.key for o in c.slots):
            chips.append(("Duplicate", "warn"))
            sub += ". Pair it again to clean this up."
        if c.via and any((r.serial, rs.index) == c.via for r, rs in hits):
            chips.append(("Connected", "ok"))
        return SlotText(main, sub, chips, s.key)

    def receiver_title(self, r: Receiver) -> Tuple[str, str]:
        nick = self.n.store.name(r.serial)
        line1 = r.kind if nick else ("OpenPuck" if r.openpuck else r.kind)
        line2 = " · ".join(x for x in (r.serial, r.firmware) if x)
        return nick or self.n.receiver(r.serial), line1 + "\n" + line2

    def controller_title(self, c: Controller) -> Tuple[str, str]:
        if c.usb:
            how = "Plugged in with USB"
        elif c.via:
            how = "Wireless, through %s" % self.n.receiver(c.via[0])
        else:
            how = "Wireless"
        return self.n.controller(c.serial), how + "\n" + " · ".join(x for x in (c.serial, c.firmware) if x)
