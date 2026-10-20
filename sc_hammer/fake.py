"""Simulated receivers and controllers for --demo and the tests.

They answer the same feature reports as the real hardware (see protocol.py), so the scanner and
actions run unchanged against them. A wireless controller is connected to the first receiver slot
whose key matches the bond in its active slot."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Set

from . import protocol as p
from .transport import VALVE_VID, IfaceInfo, UsbDevice


@dataclass
class FakeReceiver:
    serial: str
    pid: int = 0x1304
    openpuck: bool = False
    slots: List[bytes] = field(default_factory=lambda: [p.EMPTY_RECORD] * 4)
    build: int = 1754006400
    commit: str = "3f9c2a71be04"


@dataclass
class FakeController:
    serial: str
    bonds: List[Optional[bytes]] = field(default_factory=lambda: [None, None])
    active: int = 0
    usb: bool = False
    powered: bool = True
    build: int = 1756684800
    commit: str = "a81d40c97e5f"
    staged: dict = field(default_factory=dict)


def _attrs(pid: int, build: int) -> bytes:
    return struct.pack("<BLBL", 1, pid, p.ATTR_FIRMWARE_BUILD, build)


def _reply(rid: int, cmd: int, payload: bytes) -> bytes:
    out = bytes([rid, cmd, len(payload)]) + payload
    return out + bytes(max(0, 64 - len(out)))


class World:
    def __init__(self, receivers: List[FakeReceiver], controllers: List[FakeController]):
        self.receivers = receivers
        self.controllers = controllers
        self.denied: Set[str] = set()

    def link_of(self, c: FakeController):
        """(receiver, slot index) the wireless controller is connected to, or None."""
        if c.usb or not c.powered:
            return None
        bond = c.bonds[c.active]
        if not bond or not p.record_used(bond):
            return None
        for r in self.receivers:
            for i, rec in enumerate(r.slots):
                if p.record_key(rec) == p.record_key(bond):
                    return r, i
        return None

    def connected(self, r: FakeReceiver, idx: int) -> Optional[FakeController]:
        for c in self.controllers:
            link = self.link_of(c)
            if link and link[0] is r and link[1] == idx:
                return c
        return None

    def controller_reply(self, c: FakeController, cmd: int, payload: bytes) -> Optional[bytes]:
        if cmd == p.CMD_GET_STRING:
            idx = payload[0] if payload else 1
            s = {1: c.serial, 3: c.commit}.get(idx, "NA").encode()
            return _reply(1, cmd, bytes([idx]) + s)
        if cmd == p.CMD_GET_ATTRIBUTES:
            return _reply(1, cmd, _attrs(0x1302, c.build))
        if cmd == p.CMD_SETTING_READ:
            name = payload.split(b"\x00")[0].decode()
            if name == p.TRANSPORT_SETTING:
                return _reply(1, cmd, bytes([2 + c.active]))
            if name in p.BOND_SETTINGS:
                bond = c.bonds[p.BOND_SETTINGS.index(name)]
                return _reply(1, cmd, bond if bond is not None else b"")
            return _reply(1, cmd, b"")
        if cmd == p.CMD_SETTING_WRITE:
            name, _, value = payload.partition(b"\x00")
            c.staged[name.decode()] = value[:p.RECORD_LEN]
            return _reply(1, cmd, b"")
        if cmd == p.CMD_SETTING_DELETE:
            name = payload.split(b"\x00")[0].decode()
            if name in p.BOND_SETTINGS:
                c.bonds[p.BOND_SETTINGS.index(name)] = None
            return _reply(1, cmd, b"")
        if cmd == p.CMD_SETTING_COMMIT:
            name = payload.split(b"\x00")[0].decode()
            if name in c.staged and name in p.BOND_SETTINGS:
                c.bonds[p.BOND_SETTINGS.index(name)] = c.staged.pop(name)
            elif name in c.staged and name == p.TRANSPORT_SETTING:
                c.active = p.TRANSPORT_TO_SLOT[c.staged.pop(name)[0]]
            return _reply(1, cmd, b"")
        if cmd == p.CMD_TURN_OFF:
            if payload[:4] == p.TURN_OFF_MAGIC:
                c.powered = False
            return None
        if cmd == p.CMD_REBOOT:
            if payload[:4] == p.REBOOT_TO_WIRELESS:
                c.usb = False
            return None
        return _reply(1, cmd, payload)


class _Iface:
    def __init__(self, world: World):
        self.world = world
        self.resp = {}

    def get_feature(self, rid: int, max_len: int = 64) -> Optional[bytes]:
        r = self.resp.get(rid)
        return r[:max_len] if r else None

    def close(self) -> None:
        pass


class FakeReceiverIface(_Iface):
    def __init__(self, world: World, r: FakeReceiver, idx: int):
        super().__init__(world)
        self.r = r
        self.idx = idx

    def set_feature(self, rid: int, cmd: int, payload: bytes = b"") -> None:
        if rid == 1:
            c = self.world.connected(self.r, self.idx)
            # No controller on this slot: the real receiver stalls the GET.
            self.resp[1] = self.world.controller_reply(c, cmd, payload) if c else None
            return
        r = self.r
        if cmd == p.CMD_GET_STRING:
            idx = payload[0] if payload else 1
            commit = ("OpenPuck v1.4.0 " if r.openpuck else "") + r.commit
            s = {1: r.serial, 3: commit}.get(idx, "NA").encode()
            self.resp[2] = _reply(2, cmd, bytes([idx]) + s)
        elif cmd == p.CMD_GET_ATTRIBUTES:
            self.resp[2] = _reply(2, cmd, _attrs(r.pid, r.build))
        elif cmd == p.CMD_RECEIVER_SLOT_READ:
            self.resp[2] = _reply(2, cmd, r.slots[self.idx])
        elif cmd == p.CMD_RECEIVER_SLOT_WRITE:
            if len(payload) >= p.RECORD_LEN:
                r.slots[self.idx] = bytes(payload[:p.RECORD_LEN])
            self.resp[2] = _reply(2, cmd, b"")
        elif cmd == p.CMD_RECEIVER_SLOT_STATE:
            on = self.world.connected(r, self.idx) is not None
            self.resp[2] = _reply(2, cmd, bytes([2 if on else 1]))
        else:
            self.resp[2] = _reply(2, cmd, payload)


class FakeControllerIface(_Iface):
    def __init__(self, world: World, c: FakeController):
        super().__init__(world)
        self.c = c

    def set_feature(self, rid: int, cmd: int, payload: bytes = b"") -> None:
        if not self.c.usb:
            raise OSError(19, "No such device")
        name, _, value = payload.partition(b"\x00")
        if cmd == p.CMD_SETTING_WRITE and name.decode() in p.BOND_SETTINGS and \
                not p.record_used(value):
            raise BrokenPipeError(32, "Broken pipe")  # what a real controller did
        self.resp[1] = self.world.controller_reply(self.c, cmd, payload) if rid == 1 else None


class FakeTransport:
    name = "demo"

    def __init__(self, world: World):
        self.world = world

    def signature(self) -> tuple:
        return tuple(sorted([r.serial for r in self.world.receivers] +
                            [c.serial for c in self.world.controllers if c.usb]))

    def enumerate(self) -> List[UsbDevice]:
        out = []
        for r in self.world.receivers:
            product = "Steam Machine" if r.pid == 0x1305 else "Steam Controller Puck"
            dev = UsbDevice("fake/" + r.serial, VALVE_VID, r.pid, 2, r.serial, product)
            dev.ifaces.append(IfaceInfo("fake:%s:vendor" % r.serial, 0, 0xFF00, (3, 0, 0)))
            for i in range(len(r.slots)):
                dev.ifaces.append(IfaceInfo("fake:%s:%d" % (r.serial, i), i + 1, 0x01, (3, 0, 0)))
            if r.openpuck:
                dev.ifaces.append(IfaceInfo("fake:%s:wake" % r.serial, 6, 0x01, (3, 1, 2),
                                            "OpenPuck Wake"))
            out.append(dev)
        for c in self.world.controllers:
            if c.usb:
                dev = UsbDevice("fake/" + c.serial, VALVE_VID, 0x1302, 1, c.serial,
                                "Steam Controller")
                dev.ifaces.append(IfaceInfo("fake:%s:ctrl" % c.serial, 2, 0x01, (3, 0, 0)))
                out.append(dev)
        return out

    def open(self, node: str):
        if node in self.world.denied:
            raise PermissionError(13, "Permission denied", node)
        _, serial, which = node.split(":")
        for r in self.world.receivers:
            if r.serial == serial and which.isdigit():
                return FakeReceiverIface(self.world, r, int(which))
        for c in self.world.controllers:
            if c.serial == serial:
                return FakeControllerIface(self.world, c)
        raise FileNotFoundError(node)


def demo_world() -> World:
    """Two receivers and four controllers in a typical, slightly messy state."""
    k = [bytes.fromhex(h) for h in (
        "1c7a52e9b40d6f31", "8e03d5a6721bc94f", "f05b913e6ca2d877", "2ad9c4708b51e36e")]
    living = FakeReceiver("FXA941021C8B7", slots=[
        p.make_record(k[0], "FXB99602A1B2C"),
        p.make_record(k[1], "FXB99602D4E5F"),
        p.make_record(k[3], "FXB99511Q7R8S"),
        p.EMPTY_RECORD,
    ])
    office = FakeReceiver("OPK-7F3A21C9", openpuck=True, slots=[
        p.make_record(k[2], "FXB99602G7H8J"),
        p.EMPTY_RECORD,
        p.EMPTY_RECORD,
        p.EMPTY_RECORD,
    ])
    controllers = [
        FakeController("FXB99602A1B2C", bonds=[p.make_record(k[0], living.serial), None]),
        FakeController("FXB99602D4E5F", bonds=[p.make_record(k[1], living.serial), None]),
        FakeController("FXB99602G7H8J", usb=True,
                       bonds=[p.make_record(k[2], office.serial), p.EMPTY_RECORD]),
        FakeController("FXB99602K2L3M", usb=True, bonds=[p.EMPTY_RECORD, p.EMPTY_RECORD]),
    ]
    return World([living, office], controllers)
