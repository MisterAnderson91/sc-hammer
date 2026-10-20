"""Linux backend: Valve devices via /dev/hidraw* and sysfs.

Enumeration only reads sysfs (world-readable), so a missing permission shows up later as a
PermissionError from open() and can be reported per device.

Parts of this file are adapted from OpenPuck's ReversePuck/scpair.py
(https://github.com/safijari/openpuck), original code by safijari, Steam Machine, slot cloning
and wireless controller support by Leseratte10, licensed AGPL-3.0. Modified for this project
in 2026: ioctl helpers and feature-report I/O kept; sysfs-based device grouping and
report-descriptor parsing rewritten.
"""
from __future__ import annotations

try:
    import fcntl
except ImportError:  # Windows: this module's parser is still used by the tests
    fcntl = None
import glob
import os
import time
from typing import Dict, List, Optional

from .transport import VALVE_VID, IfaceInfo, UsbDevice

_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def HIDIOCSFEATURE(size: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, 0x06, size)


def HIDIOCGFEATURE(size: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, 0x07, size)


class HidrawIface:
    def __init__(self, node: str):
        self.node = node
        self.fd = os.open(node, os.O_RDWR)

    def set_feature(self, rid: int, cmd: int, payload: bytes = b"") -> None:
        buf = bytearray(64)
        buf[0] = rid
        buf[1] = cmd
        buf[2] = len(payload)
        buf[3:3 + len(payload)] = payload
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(64), buf, True)

    def get_feature(self, rid: int, max_len: int = 64) -> Optional[bytes]:
        # Relayed (wireless) answers arrive a poll or two later; the receiver stalls until then.
        for _ in range(3):
            buf = bytearray(max_len)
            buf[0] = rid
            try:
                fcntl.ioctl(self.fd, HIDIOCGFEATURE(max_len), buf, True)
                return bytes(buf)
            except OSError:
                time.sleep(0.02)
        return None

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


def _read(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().decode("latin1", "replace").strip()
    except OSError:
        return ""


def _hex(path: str) -> Optional[int]:
    try:
        return int(_read(path), 16)
    except ValueError:
        return None


def first_usage_page(desc: bytes) -> Optional[int]:
    """First Usage Page item of a HID report descriptor (0x01 = generic desktop, 0xFF00 = vendor)."""
    i = 0
    while i < len(desc):
        b = desc[i]
        if b == 0x05 and i + 1 < len(desc):
            return desc[i + 1]
        if b == 0x06 and i + 2 < len(desc):
            return desc[i + 1] | (desc[i + 2] << 8)
        if b == 0xFE:  # long item
            if i + 1 >= len(desc):
                break
            i += 3 + desc[i + 1]
            continue
        size = b & 0x03
        i += 1 + (4 if size == 3 else size)
    return None


class LinuxTransport:
    name = "hidraw"

    def signature(self) -> tuple:
        return tuple(sorted(glob.glob("/dev/hidraw*")))

    def enumerate(self) -> List[UsbDevice]:
        devices: Dict[str, UsbDevice] = {}
        for node in sorted(glob.glob("/dev/hidraw*")):
            name = os.path.basename(node)
            hid_dir = os.path.realpath("/sys/class/hidraw/%s/device" % name)
            iface_dir = os.path.dirname(hid_dir)
            usb_dir = os.path.dirname(iface_dir)
            # Bluetooth and uhid devices have no USB interface/device directories.
            if _hex(os.path.join(usb_dir, "idVendor")) != VALVE_VID:
                continue
            try:
                with open(os.path.join(hid_dir, "report_descriptor"), "rb") as f:
                    usage_page = first_usage_page(f.read())
            except OSError:
                usage_page = None
            dev = devices.get(usb_dir)
            if dev is None:
                dev = UsbDevice(
                    id=usb_dir,
                    vid=VALVE_VID,
                    pid=_hex(os.path.join(usb_dir, "idProduct")) or 0,
                    bcd=_hex(os.path.join(usb_dir, "bcdDevice")) or 0,
                    serial=_read(os.path.join(usb_dir, "serial")),
                    product=_read(os.path.join(usb_dir, "product")),
                )
                devices[usb_dir] = dev
            cls = tuple(_hex(os.path.join(iface_dir, f)) or 0
                        for f in ("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"))
            dev.ifaces.append(IfaceInfo(
                node=node,
                number=_hex(os.path.join(iface_dir, "bInterfaceNumber")) or 0,
                usage_page=usage_page,
                hid_class=cls,  # type: ignore[arg-type]
                name=_read(os.path.join(iface_dir, "interface")),
            ))
        # Other interfaces of the same USB device can carry the "OpenPuck" name without a hidraw
        # node (WebUSB, CDC), so look at all of them.
        for usb_dir, dev in devices.items():
            for path in glob.glob(os.path.join(usb_dir, "*:*", "interface")):
                if "OpenPuck" in _read(path) and not dev.is_openpuck:
                    dev.ifaces.append(IfaceInfo(node="", number=-1, usage_page=None,
                                                hid_class=(0, 0, 0), name=_read(path)))
        return list(devices.values())

    def open(self, node: str) -> HidrawIface:
        return HidrawIface(node)
