"""What a device backend has to provide: enumeration of Valve USB devices and their HID interfaces,
and a way to open one interface for feature reports. hidraw.py is the real one, fake.py the demo."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

VALVE_VID = 0x28DE


@dataclass
class IfaceInfo:
    node: str
    number: int
    usage_page: Optional[int]
    hid_class: Tuple[int, int, int]
    name: str = ""

    @property
    def is_command_iface(self) -> bool:
        """The interface that carries the Valve command channel. Boot keyboards/mice (OpenPuck's
        wake mouse, lizard mode) and the vendor 0xFF00 interface are skipped."""
        return self.hid_class == (3, 0, 0) and self.usage_page in (0x01, None)


@dataclass
class UsbDevice:
    id: str
    vid: int
    pid: int
    bcd: int
    serial: str
    product: str
    ifaces: List[IfaceInfo] = field(default_factory=list)

    @property
    def is_openpuck(self) -> bool:
        return any("OpenPuck" in i.name for i in self.ifaces)

    def command_ifaces(self) -> List[IfaceInfo]:
        return sorted((i for i in self.ifaces if i.is_command_iface), key=lambda i: i.number)


USAGE_MOUSE, USAGE_KEYBOARD = 0x02, 0x06


def _is_mouse_or_keyboard(c: dict) -> bool:
    return c["usage_page"] == 0x01 and c["usage"] in (USAGE_MOUSE, USAGE_KEYBOARD)


def group_collections(collections: List[dict]) -> Tuple[List[UsbDevice], Dict[str, int]]:
    """Turn Windows HID collections back into USB devices and interfaces.

    Windows splits one USB interface into collections: a puck slot is a mouse, a keyboard and a
    vendor collection with the 64-byte feature reports. Like Linux, an interface is judged by its
    first collection's usage page (so the puck's vendor-only control interface is left out), and
    the app talks through the collection that has feature reports and isn't a mouse or keyboard.
    Each dict needs path, pid, version, serial, product, mi, col, usage_page, usage, feature_len.
    Returns the devices and each command collection's feature report length."""
    devices: Dict[tuple, UsbDevice] = {}
    groups: Dict[tuple, List[dict]] = {}
    for c in collections:
        key = (c["pid"], c["serial"] or c["path"])
        if key not in devices:
            devices[key] = UsbDevice(id=c["path"], vid=VALVE_VID, pid=c["pid"], bcd=c["version"],
                                     serial=c["serial"], product=c["product"])
        groups.setdefault(key + (c["mi"],), []).append(c)
    report_len: Dict[str, int] = {}
    for gkey, cols in groups.items():
        cols.sort(key=lambda c: c["col"])
        cmd = next((c for c in cols if c["feature_len"] >= 4 and not _is_mouse_or_keyboard(c)),
                   None)
        if cmd is None:
            continue
        report_len[cmd["path"]] = cmd["feature_len"]
        devices[gkey[:2]].ifaces.append(IfaceInfo(
            node=cmd["path"], number=cols[0]["mi"], usage_page=cols[0]["usage_page"],
            hid_class=(3, 0, 0), name=cols[0]["product"]))
    return list(devices.values()), report_len
