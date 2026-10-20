"""What a device backend has to provide: enumeration of Valve USB devices and their HID interfaces,
and a way to open one interface for feature reports. hidraw.py is the real one, fake.py the demo."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

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
