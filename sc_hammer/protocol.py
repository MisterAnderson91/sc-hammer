"""Valve feature-report commands used for pairing.

Every command is a 64-byte HID feature report laid out as [report id][cmd][len][payload...].
Receivers (pucks and the Steam Machine's built-in receiver) answer on report id 2. Report id 1
sent to a receiver slot is relayed over the air to the controller connected on that slot. A
controller plugged in with USB answers on report id 1.

Parts of this file are adapted from OpenPuck's ReversePuck/scpair.py
(https://github.com/safijari/openpuck), original code by safijari, Steam Machine, slot cloning
and wireless controller support by Leseratte10, licensed AGPL-3.0. Modified for this project
in 2026: restructured into standalone functions, echo/stall handling, bond deletion (0xF0),
active-slot writes, ReversePuck bond slots (rp/info, rp/bond/N).
The command layout also follows OpenPuck's pairtui/pairtui.py and docs/PROTOCOL.md.
"""
from __future__ import annotations

import os
import struct
import time
from typing import Optional, Protocol

CMD_GET_ATTRIBUTES = 0x83
CMD_GET_ATTRIBUTES_LEGACY = 0xA6
CMD_GET_STRING = 0xAE
CMD_GET_STRING_LEGACY = 0xA4
CMD_RECEIVER_SLOT_WRITE = 0xA2
CMD_RECEIVER_SLOT_READ = 0xA3
CMD_RECEIVER_SLOT_STATE = 0xB4
CMD_SETTING_READ = 0xED
CMD_SETTING_WRITE = 0xEE
CMD_SETTING_COMMIT = 0xEF
CMD_SETTING_DELETE = 0xF0
CMD_REBOOT = 0x95
CMD_TURN_OFF = 0x9F

REBOOT_TO_WIRELESS = bytes([0x52, 0xAF, 0x27, 0xA4])
TURN_OFF_MAGIC = b"off!"

ATTR_FIRMWARE_BUILD = 4
STRING_SERIAL = 1
STRING_FIRMWARE_COMMIT = 3

# Controller bond slots are named settings; the active one is picked by user/wireless_transport
# (2 = slot 0, 3 = slot 1).
BOND_SETTINGS = ("esb/bond", "esb/bond_2")
TRANSPORT_SETTING = "user/wireless_transport"
TRANSPORT_TO_SLOT = {2: 0, 3: 1}

# A ReversePuck (OpenPuck's Steam Deck dongle, also 28DE:1302) answers "rp/info" with
# ['R']['P'][version][slot count] and keeps its bonds in "rp/bond/0", "rp/bond/1", ..., each written
# to exactly that slot. A real controller has no such setting and answers it empty.
RP_INFO_SETTING = "rp/info"
RP_BOND_SETTING = "rp/bond/%d"

# A bond record is [8-byte pairing key][16-byte ASCII serial of the other side]. The key is all
# that matters for the radio link; the serial is only a label.
KEY_LEN = 8
NAME_LEN = 16
RECORD_LEN = KEY_LEN + NAME_LEN
EMPTY_RECORD = bytes(RECORD_LEN)


class Iface(Protocol):
    def set_feature(self, rid: int, cmd: int, payload: bytes = b"") -> None: ...

    def get_feature(self, rid: int, max_len: int = 64) -> Optional[bytes]: ...


def make_record(key: bytes, name: str) -> bytes:
    raw = name.encode("latin1", "replace")[:NAME_LEN]
    return bytes(key[:KEY_LEN]) + raw + bytes(NAME_LEN - len(raw))


def record_key(rec: bytes) -> bytes:
    return bytes(rec[:KEY_LEN])


def record_name(rec: bytes) -> str:
    return rec[KEY_LEN:RECORD_LEN].split(b"\x00")[0].decode("latin1", "replace").strip()


def record_used(rec: bytes) -> bool:
    return len(rec) >= KEY_LEN and any(rec[:KEY_LEN])


def new_key() -> bytes:
    while True:
        key = os.urandom(KEY_LEN)
        if any(key):
            return key


def query(iface: Iface, rid: int, cmd: int, payload: bytes = b"", max_len: int = 64,
          attempts: int = 5) -> Optional[bytes]:
    """SET then GET; retry until the reply echoes our report id and command.

    Steam talks to the same devices, and a relayed controller may drop mid-request, so a
    reply for a different command is retried rather than trusted."""
    for _ in range(attempts):
        try:
            iface.set_feature(rid, cmd, payload)
        except OSError:
            time.sleep(0.03)
            continue
        r = iface.get_feature(rid, max_len)
        if r and len(r) >= 3 and r[0] == rid and r[1] == cmd:
            return r
        time.sleep(0.03)
    return None


def _payload(r: bytes) -> bytes:
    n = r[2]
    if 0 < n <= len(r) - 3:
        return r[3:3 + n]
    return r[3:]


def read_string(iface: Iface, rid: int, index: int, legacy: bool = False) -> Optional[str]:
    if legacy:
        r = query(iface, 1, CMD_GET_STRING_LEGACY, bytes([index]))
    else:
        r = query(iface, rid, CMD_GET_STRING, bytes([index]))
    if r is None or len(r) < 5:
        return None
    s = r[4:].split(b"\x00")[0].decode("latin1", "replace").strip()
    return s or None


def read_attributes(iface: Iface, rid: int, cmd: int = CMD_GET_ATTRIBUTES) -> dict:
    r = query(iface, rid, cmd, max_len=256)
    if r is None:
        return {}
    data = _payload(r)
    attrs = {}
    for off in range(0, len(data) - 4, 5):
        tag, val = struct.unpack_from("<BL", data, off)
        attrs.setdefault(tag, val)
    return attrs


def describe_firmware(build: Optional[int], commit: Optional[str]) -> str:
    """Human-readable firmware: OpenPuck's version string, else the build date."""
    if commit and commit.startswith("OpenPuck"):
        return commit
    if build:
        # The build attribute is a Unix timestamp on every device seen so far.
        if 1420070400 <= build <= 2208988800:
            return time.strftime("built %Y-%m-%d", time.gmtime(build))
        return "build %X" % build
    return ""


# --- receivers (report id 2, one HID interface per slot) ---

def read_receiver_slot(iface: Iface) -> Optional[bytes]:
    r = query(iface, 2, CMD_RECEIVER_SLOT_READ)
    if r is None or len(r) < 3 + RECORD_LEN:
        return None
    return bytes(r[3:3 + RECORD_LEN])


def receiver_slot_connected(iface: Iface) -> Optional[bool]:
    r = query(iface, 2, CMD_RECEIVER_SLOT_STATE)
    if r is None or len(r) < 4:
        return None
    return r[3] == 2


def write_receiver_slot(iface: Iface, rec: bytes) -> None:
    """0xA2: a zero record clears the slot, anything else is stored verbatim."""
    iface.set_feature(2, CMD_RECEIVER_SLOT_WRITE, bytes(rec[:RECORD_LEN]))


# --- controllers (report id 1, over USB or relayed through a receiver slot) ---

def read_setting(iface: Iface, name: str, attempts: int = 5) -> Optional[bytes]:
    req = name.encode() + b"\x00"
    r = query(iface, 1, CMD_SETTING_READ, req, attempts=attempts)
    if r is None:
        return None
    data = r[3:3 + r[2]] if r[2] <= len(r) - 3 else r[3:]
    # OpenPuck echoes the request back when it couldn't relay it; that's not an answer.
    if data[:len(req)] == req:
        return None
    return bytes(data)


def bond_setting(slot: int, reversepuck: bool = False) -> str:
    """The setting a controller keeps bond slot `slot` in."""
    return RP_BOND_SETTING % slot if reversepuck else BOND_SETTINGS[slot]


def read_reversepuck_slots(iface: Iface, attempts: int = 2) -> Optional[int]:
    """How many bond slots a ReversePuck has, or None if this isn't one."""
    data = read_setting(iface, RP_INFO_SETTING, attempts)
    if data and len(data) >= 4 and data[:2] == b"RP":
        return data[3]
    return None


def read_controller_bond(iface: Iface, slot: int, attempts: int = 5,
                         reversepuck: bool = False) -> Optional[bytes]:
    """24-byte record (zeros when empty), or None if the read failed."""
    data = read_setting(iface, bond_setting(slot, reversepuck), attempts)
    if data is None:
        return None
    rec = data[:RECORD_LEN]
    return rec + bytes(RECORD_LEN - len(rec))


def read_active_slot(iface: Iface, attempts: int = 5) -> Optional[int]:
    data = read_setting(iface, TRANSPORT_SETTING, attempts)
    if not data:
        return None
    return TRANSPORT_TO_SLOT.get(data[0])


def write_active_slot(iface: Iface, slot: int) -> None:
    """Pick which bond slot the controller uses when it's wireless (user/wireless_transport)."""
    value = {v: k for k, v in TRANSPORT_TO_SLOT.items()}[slot]
    name = TRANSPORT_SETTING.encode() + b"\x00"
    iface.set_feature(1, CMD_SETTING_WRITE, name + bytes([value]))
    iface.set_feature(1, CMD_SETTING_COMMIT, name)


def write_controller_bond(iface: Iface, slot: int, rec: bytes, reversepuck: bool = False) -> None:
    """0xEE stages the setting, 0xEF commits it. An empty record deletes the setting (0xF0)
    instead: controllers stall (EPIPE) an 0xEE write of an all-zero bond."""
    name = bond_setting(slot, reversepuck).encode() + b"\x00"
    if not record_used(rec):
        iface.set_feature(1, CMD_SETTING_DELETE, name)
        return
    iface.set_feature(1, CMD_SETTING_WRITE, name + bytes(rec[:RECORD_LEN]))
    iface.set_feature(1, CMD_SETTING_COMMIT, name)


def reboot_controller(iface: Iface, to_wireless: bool) -> None:
    iface.set_feature(1, CMD_REBOOT, REBOOT_TO_WIRELESS if to_wireless else b"")


def turn_off_controller(iface: Iface) -> None:
    """The same "off!" command Steam sends; through a receiver slot it's relayed over the air."""
    iface.set_feature(1, CMD_TURN_OFF, TURN_OFF_MAGIC)
