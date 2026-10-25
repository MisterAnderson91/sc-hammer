"""Reads every receiver and controller into a Snapshot the GUI can show and actions.py can plan on."""
from __future__ import annotations

import errno
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import protocol as p
from .transport import UsbDevice

RECEIVER_PIDS = {0x1304: "Steam Controller Puck", 0x1305: "Steam Machine receiver"}
# 0x1205 (Steam Deck built-in controls) has no wireless bonds and is left alone.
CONTROLLER_PIDS = {0x1301, 0x1302, 0x1303}
# A ReversePuck dongle (the Steam Deck as a Steam Controller) shows up as a wired controller; it says
# what it is when asked (protocol.read_reversepuck_slots) and has numbered slots, not right/left.
REVERSEPUCK_PID = 0x1302
REVERSEPUCK_MAX_SLOTS = 16

USED, EMPTY, UNKNOWN = "used", "empty", "unknown"

# Steam calls a controller's two puck slots right and left (esb/bond is the one Steam pairs into,
# the R1 slot). Turning the controller on while holding the combo picks that slot.
CONTROLLER_SLOT_NAMES = ("right slot", "left slot")
CONTROLLER_SLOT_COMBOS = ("A + R1 + Steam", "A + L1 + Steam")


def slot_name(is_controller: bool, index: int, reversepuck: bool = False) -> str:
    """"slot 3" on a receiver or a ReversePuck, "right slot" on a controller."""
    if is_controller and not reversepuck and index < len(CONTROLLER_SLOT_NAMES):
        return CONTROLLER_SLOT_NAMES[index]
    return "slot %d" % (index + 1)


def _state(rec: Optional[bytes]) -> str:
    if rec is None:
        return UNKNOWN
    return USED if p.record_used(rec) else EMPTY


@dataclass
class ReceiverSlot:
    index: int
    record: Optional[bytes]
    connected: Optional[bool] = None
    connected_serial: Optional[str] = None

    @property
    def state(self) -> str:
        return _state(self.record)

    @property
    def key(self) -> bytes:
        return p.record_key(self.record) if self.state == USED else b""

    @property
    def name(self) -> str:
        """Serial of the controller this slot was paired with."""
        return p.record_name(self.record) if self.state == USED else ""


@dataclass
class Receiver:
    serial: str
    kind: str
    firmware: str
    openpuck: bool
    slots: List[ReceiverSlot]
    ifaces: List[Any] = field(default_factory=list, repr=False, compare=False)


@dataclass
class ControllerSlot:
    index: int
    record: Optional[bytes]
    active: bool = False

    @property
    def state(self) -> str:
        return _state(self.record)

    @property
    def key(self) -> bytes:
        return p.record_key(self.record) if self.state == USED else b""

    @property
    def name(self) -> str:
        """Serial of the receiver this slot was paired with."""
        return p.record_name(self.record) if self.state == USED else ""


@dataclass
class Controller:
    serial: str
    usb: bool
    firmware: str
    slots: List[ControllerSlot]
    active: Optional[int] = None
    via: Optional[Tuple[str, int]] = None  # (receiver serial, slot) when wireless
    iface: Any = field(default=None, repr=False, compare=False)
    reversepuck: bool = False  # a ReversePuck dongle: numbered slots, no active slot


@dataclass
class Problem:
    title: str
    detail: str
    kind: str = "info"  # "permission" gets the how-to-fix help


@dataclass
class Snapshot:
    receivers: List[Receiver] = field(default_factory=list)
    controllers: List[Controller] = field(default_factory=list)
    problems: List[Problem] = field(default_factory=list)

    def receiver(self, serial: str) -> Optional[Receiver]:
        return next((r for r in self.receivers if r.serial == serial), None)

    def controller(self, serial: str) -> Optional[Controller]:
        return next((c for c in self.controllers if c.serial == serial), None)

    def receivers_with_key(self, key: bytes) -> List[Tuple[Receiver, ReceiverSlot]]:
        if not key:
            return []
        return [(r, s) for r in self.receivers for s in r.slots if s.key == key]

    def controllers_with_key(self, key: bytes) -> List[Tuple[Controller, ControllerSlot]]:
        if not key:
            return []
        return [(c, s) for c in self.controllers for s in c.slots if s.key == key]


class Scanner:
    """Opens devices through a transport and reads them. Keeps interfaces open until the next
    scan so actions can write through them."""

    def __init__(self, transport):
        self.transport = transport
        self._open: List[Any] = []

    def close(self) -> None:
        for f in self._open:
            close = getattr(f, "close", None)
            if close:
                close()
        self._open = []

    def _open_iface(self, node: str):
        f = self.transport.open(node)
        self._open.append(f)
        return f

    def scan(self) -> Snapshot:
        self.close()
        snap = Snapshot()
        denied: List[str] = []
        for dev in self.transport.enumerate():
            try:
                if dev.pid in RECEIVER_PIDS:
                    self._scan_receiver(dev, snap)
                elif dev.pid in CONTROLLER_PIDS:
                    self._scan_usb_controller(dev, snap)
            except PermissionError:
                denied.append(dev.product or "Valve device %04X" % dev.pid)
            except OSError as e:
                if e.errno in (errno.EACCES, errno.EPERM):
                    denied.append(dev.product or "Valve device %04X" % dev.pid)
                else:
                    snap.problems.append(Problem(
                        "Couldn't talk to %s" % (dev.product or "a Valve device"), str(e)))
        for r in snap.receivers:
            self._scan_wireless_controllers(r, snap)
        if denied and sys.platform == "win32":
            snap.problems.append(Problem(
                "Couldn't open: " + ", ".join(sorted(set(denied))),
                "Windows wouldn't let SC Hammer use it. Close other controller tools (Steam can "
                "stay open), unplug it and plug it back in, then click Refresh.", "blocked"))
        elif denied:
            snap.problems.append(Problem(
                "No permission to use: " + ", ".join(sorted(set(denied))),
                "This user account isn't allowed to open the device. See \"Fix permissions\".",
                "permission"))
        return snap

    def _scan_receiver(self, dev: UsbDevice, snap: Snapshot) -> None:
        infos = dev.command_ifaces()
        if not infos:
            snap.problems.append(Problem(
                "Found %s, but couldn't find its pairing interface" %
                (dev.product or "a receiver"),
                "Please report this: run SC Hammer with --diagnose and attach the "
                "sc-hammer-diagnose.txt file it writes."))
            return
        ifaces = [self._open_iface(i.node) for i in infos]
        legacy = dev.bcd != 2
        first = ifaces[0]
        serial = p.read_string(first, 2, p.STRING_SERIAL, legacy) or dev.serial or dev.id
        commit = p.read_string(first, 2, p.STRING_FIRMWARE_COMMIT, legacy)
        attrs = p.read_attributes(first, 1 if legacy else 2,
                                  p.CMD_GET_ATTRIBUTES_LEGACY if legacy else p.CMD_GET_ATTRIBUTES)
        openpuck = dev.is_openpuck or bool(commit and commit.startswith("OpenPuck"))
        kind = RECEIVER_PIDS[dev.pid]
        if openpuck:
            kind = "OpenPuck" if dev.pid == 0x1304 else "OpenPuck (Steam Machine mode)"
        slots = []
        for idx, f in enumerate(ifaces):
            rec = p.read_receiver_slot(f)
            slots.append(ReceiverSlot(idx, rec, p.receiver_slot_connected(f) if rec else None))
        snap.receivers.append(Receiver(
            serial=serial, kind=kind,
            firmware=p.describe_firmware(attrs.get(p.ATTR_FIRMWARE_BUILD), commit),
            openpuck=openpuck, slots=slots, ifaces=ifaces))

    def _read_controller(self, f, attempts: int) -> Tuple[List[ControllerSlot], Optional[int], str]:
        active = p.read_active_slot(f, attempts)
        slots = [ControllerSlot(i, p.read_controller_bond(f, i, attempts), active == i)
                 for i in range(len(p.BOND_SETTINGS))]
        attrs = p.read_attributes(f, 1)
        commit = p.read_string(f, 1, p.STRING_FIRMWARE_COMMIT)
        return slots, active, p.describe_firmware(attrs.get(p.ATTR_FIRMWARE_BUILD), commit)

    def _read_reversepuck(self, f, count: int) -> List[ControllerSlot]:
        # Every slot is live at once (the dongle connects to whichever puck is in range), so there's
        # no active slot; and its firmware build is a stand-in, not worth showing.
        return [ControllerSlot(i, p.read_controller_bond(f, i, reversepuck=True))
                for i in range(min(count, REVERSEPUCK_MAX_SLOTS))]

    def _scan_usb_controller(self, dev: UsbDevice, snap: Snapshot) -> None:
        for info in dev.command_ifaces():
            f = self._open_iface(info.node)
            serial = p.read_string(f, 1, p.STRING_SERIAL)
            if not serial:
                continue
            rp_slots = p.read_reversepuck_slots(f) if dev.pid == REVERSEPUCK_PID else None
            if rp_slots:
                slots = self._read_reversepuck(f, rp_slots)
                snap.controllers.append(Controller(serial, True, "", slots, None, None, f,
                                                   reversepuck=True))
                return
            slots, active, fw = self._read_controller(f, attempts=5)
            snap.controllers.append(Controller(serial, True, fw, slots, active, None, f))
            return
        snap.problems.append(Problem(
            "A controller is plugged in but didn't answer",
            "Unplug it, wait a moment and plug it back in, then click Refresh."))

    def _scan_wireless_controllers(self, r: Receiver, snap: Snapshot) -> None:
        for slot, f in zip(r.slots, r.ifaces):
            if not slot.connected:
                continue
            serial = p.read_string(f, 1, p.STRING_SERIAL)
            slot.connected_serial = serial
            if not serial or snap.controller(serial):
                continue
            slots, active, fw = self._read_controller(f, attempts=8)
            snap.controllers.append(Controller(serial, False, fw, slots, active,
                                               (r.serial, slot.index), f))
