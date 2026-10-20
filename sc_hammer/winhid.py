"""Windows backend: Valve HID devices through SetupAPI and hid.dll (ctypes, no extra packages).

Windows lists one HID device path per top-level collection, e.g.
\\\\?\\hid#vid_28de&pid_1304&mi_01#7&1a2b3c4d&0&0000#{4d1e55b2-...}. Paths are grouped back into
USB devices by product ID and serial number, and the USB interface number comes from "mi_NN".
Feature reports have to be exactly FeatureReportByteLength long (report ID included)."""
from __future__ import annotations

import ctypes
import errno
import re
import time
from ctypes import wintypes as wt
from typing import Dict, List, Optional

from .transport import VALVE_VID, IfaceInfo, UsbDevice

_setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
_hid = ctypes.WinDLL("hid", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
INVALID_HANDLE = ctypes.c_void_p(-1).value
HIDP_STATUS_SUCCESS = 0x00110000
ERROR_ACCESS_DENIED = 5
ERROR_GEN_FAILURE = 31  # what a stalled feature request (the device said no) comes back as

USAGE_MOUSE, USAGE_KEYBOARD = 0x02, 0x06


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID), ("Flags", wt.DWORD),
                ("Reserved", ctypes.c_size_t)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wt.ULONG), ("VendorID", wt.USHORT), ("ProductID", wt.USHORT),
                ("VersionNumber", wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT),
                ("InputReportByteLength", wt.USHORT), ("OutputReportByteLength", wt.USHORT),
                ("FeatureReportByteLength", wt.USHORT), ("Reserved", wt.USHORT * 17),
                ("NumberLinkCollectionNodes", wt.USHORT), ("NumberInputButtonCaps", wt.USHORT),
                ("NumberInputValueCaps", wt.USHORT), ("NumberInputDataIndices", wt.USHORT),
                ("NumberOutputButtonCaps", wt.USHORT), ("NumberOutputValueCaps", wt.USHORT),
                ("NumberOutputDataIndices", wt.USHORT), ("NumberFeatureButtonCaps", wt.USHORT),
                ("NumberFeatureValueCaps", wt.USHORT), ("NumberFeatureDataIndices", wt.USHORT)]


def _proto(fn, restype, *argtypes):
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


_HidD_GetHidGuid = _proto(_hid.HidD_GetHidGuid, None, ctypes.POINTER(GUID))
_HidD_GetAttributes = _proto(_hid.HidD_GetAttributes, wt.BOOLEAN, wt.HANDLE,
                             ctypes.POINTER(HIDD_ATTRIBUTES))
_HidD_GetSerialNumberString = _proto(_hid.HidD_GetSerialNumberString, wt.BOOLEAN, wt.HANDLE,
                                     ctypes.c_void_p, wt.ULONG)
_HidD_GetProductString = _proto(_hid.HidD_GetProductString, wt.BOOLEAN, wt.HANDLE,
                                ctypes.c_void_p, wt.ULONG)
_HidD_GetPreparsedData = _proto(_hid.HidD_GetPreparsedData, wt.BOOLEAN, wt.HANDLE,
                                ctypes.POINTER(ctypes.c_void_p))
_HidD_FreePreparsedData = _proto(_hid.HidD_FreePreparsedData, wt.BOOLEAN, ctypes.c_void_p)
_HidP_GetCaps = _proto(_hid.HidP_GetCaps, ctypes.c_long, ctypes.c_void_p,
                       ctypes.POINTER(HIDP_CAPS))
_HidD_SetFeature = _proto(_hid.HidD_SetFeature, wt.BOOLEAN, wt.HANDLE, ctypes.c_void_p, wt.ULONG)
_HidD_GetFeature = _proto(_hid.HidD_GetFeature, wt.BOOLEAN, wt.HANDLE, ctypes.c_void_p, wt.ULONG)

_SetupDiGetClassDevsW = _proto(_setupapi.SetupDiGetClassDevsW, ctypes.c_void_p,
                               ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD)
_SetupDiEnumDeviceInterfaces = _proto(_setupapi.SetupDiEnumDeviceInterfaces, wt.BOOL,
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
                                      wt.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA))
_SetupDiGetDeviceInterfaceDetailW = _proto(_setupapi.SetupDiGetDeviceInterfaceDetailW, wt.BOOL,
                                           ctypes.c_void_p,
                                           ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                           ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                           ctypes.c_void_p)
_SetupDiDestroyDeviceInfoList = _proto(_setupapi.SetupDiDestroyDeviceInfoList, wt.BOOL,
                                       ctypes.c_void_p)

_CreateFileW = _proto(_kernel32.CreateFileW, wt.HANDLE, wt.LPCWSTR, wt.DWORD, wt.DWORD,
                      ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE)
_CloseHandle = _proto(_kernel32.CloseHandle, wt.BOOL, wt.HANDLE)


def _hid_paths() -> List[str]:
    """Device paths of every present HID collection."""
    guid = GUID()
    _HidD_GetHidGuid(ctypes.byref(guid))
    info = _SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                 DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if not info or info == INVALID_HANDLE:
        return []
    paths = []
    try:
        i = 0
        while True:
            data = SP_DEVICE_INTERFACE_DATA()
            data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not _SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid), i,
                                                ctypes.byref(data)):
                break
            i += 1
            size = wt.DWORD(0)
            _SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), None, 0,
                                              ctypes.byref(size), None)
            if not size.value:
                continue
            buf = ctypes.create_string_buffer(size.value)
            # SP_DEVICE_INTERFACE_DETAIL_DATA_W: a DWORD cbSize then the path; cbSize is the
            # struct's fixed size (8 on 64-bit Windows, 6 on 32-bit).
            ctypes.c_uint32.from_buffer(buf).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if _SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), buf, size, None, None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        _SetupDiDestroyDeviceInfoList(info)
    return paths


def _open(path: str, access: int):
    h = _CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if not h or h == INVALID_HANDLE:
        err = ctypes.get_last_error()
        if err == ERROR_ACCESS_DENIED:
            raise PermissionError(errno.EACCES, "Access denied", path)
        raise OSError(errno.EIO, ctypes.FormatError(err).strip() or "CreateFile failed", path)
    return h


def _string(fn, h) -> str:
    buf = ctypes.create_unicode_buffer(128)
    if fn(h, buf, ctypes.sizeof(buf)):
        return buf.value.strip()
    return ""


def _caps(h) -> Optional[HIDP_CAPS]:
    pp = ctypes.c_void_p()
    if not _HidD_GetPreparsedData(h, ctypes.byref(pp)):
        return None
    try:
        caps = HIDP_CAPS()
        if _HidP_GetCaps(pp, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
            return None
        return caps
    finally:
        _HidD_FreePreparsedData(pp)


class WinHidIface:
    def __init__(self, path: str, report_len: int):
        self.node = path
        self.report_len = report_len
        self.handle = _open(path, GENERIC_READ | GENERIC_WRITE)

    def set_feature(self, rid: int, cmd: int, payload: bytes = b"") -> None:
        buf = (ctypes.c_ubyte * self.report_len)()
        buf[0] = rid
        buf[1] = cmd
        buf[2] = len(payload)
        for i, b in enumerate(payload[:self.report_len - 3]):
            buf[3 + i] = b
        if not _HidD_SetFeature(self.handle, buf, self.report_len):
            err = ctypes.get_last_error()
            if err == ERROR_GEN_FAILURE:
                raise BrokenPipeError(errno.EPIPE, "The device refused the request")
            raise OSError(errno.EIO, ctypes.FormatError(err).strip() or "HidD_SetFeature failed")

    def get_feature(self, rid: int, max_len: int = 64) -> Optional[bytes]:
        # Relayed (wireless) answers arrive a poll or two later; the receiver stalls until then.
        for _ in range(3):
            buf = (ctypes.c_ubyte * self.report_len)()
            buf[0] = rid
            if _HidD_GetFeature(self.handle, buf, self.report_len):
                return bytes(buf)
            time.sleep(0.02)
        return None

    def close(self) -> None:
        if self.handle:
            _CloseHandle(self.handle)
            self.handle = None


class WindowsTransport:
    name = "windows-hid"

    def __init__(self):
        self._report_len: Dict[str, int] = {}

    def signature(self) -> tuple:
        return tuple(sorted(p for p in _hid_paths() if "vid_28de" in p.lower()))

    def enumerate(self) -> List[UsbDevice]:
        devices: Dict[tuple, UsbDevice] = {}
        for path in _hid_paths():
            low = path.lower()
            if "vid_28de" not in low:
                continue
            try:
                h = _open(path, 0)  # query only: works even for collections Windows keeps for itself
            except OSError:
                continue
            try:
                attrs = HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
                if not _HidD_GetAttributes(h, ctypes.byref(attrs)) or attrs.VendorID != VALVE_VID:
                    continue
                caps = _caps(h)
                serial = _string(_HidD_GetSerialNumberString, h)
                product = _string(_HidD_GetProductString, h)
            finally:
                _CloseHandle(h)
            if caps is None:
                continue
            m = re.search(r"&mi_([0-9a-f]{2})", low)
            number = int(m.group(1), 16) if m else 0
            key = (attrs.ProductID, serial or path)
            dev = devices.get(key)
            if dev is None:
                dev = UsbDevice(id=path, vid=VALVE_VID, pid=attrs.ProductID,
                                bcd=attrs.VersionNumber, serial=serial, product=product)
                devices[key] = dev
            # Only collections with feature reports carry the command channel; one per interface.
            if caps.FeatureReportByteLength < 4 or any(i.number == number for i in dev.ifaces):
                continue
            # Mice and keyboards (OpenPuck's wake mouse, lizard mode) can't be opened for
            # read/write on Windows, so mark them the way Linux reports boot devices.
            if caps.UsagePage == 0x01 and caps.Usage == USAGE_MOUSE:
                hid_class = (3, 1, 2)
            elif caps.UsagePage == 0x01 and caps.Usage == USAGE_KEYBOARD:
                hid_class = (3, 1, 1)
            else:
                hid_class = (3, 0, 0)
            self._report_len[path] = caps.FeatureReportByteLength
            dev.ifaces.append(IfaceInfo(node=path, number=number, usage_page=caps.UsagePage,
                                        hid_class=hid_class, name=product))
        return list(devices.values())

    def open(self, node: str) -> WinHidIface:
        return WinHidIface(node, self._report_len.get(node, 64))
