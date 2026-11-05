"""
Audeze Maxwell 2 head tracking over HID -- protocol and device access.

The headset fuses its IMU itself and streams a unit quaternion. It is reached
either by USB cable or through the Maxwell USB dongle; both expose the same
vendor HID interface. Protocol details are in the maxwell2-ht project's README.

    Interface   vendor 0x3329, usage page 0xFF13, usage 1
    Enable      output report 06 09 RR 05 5A 05 00 00 09 2E 00 (0x80 | rate)
                RR routes the command: 00 on the cable, 80 through the dongle
    Data        input report 0A + 4 x float32 LE (w, x, y, z)
"""
from __future__ import annotations

import ctypes
import struct
import sys

import hid

VENDOR_ID = 0x3329
VENDOR_USAGE_PAGE = 0xFF13
VENDOR_USAGE = 1

PID_HEADSET = 0x4BAA      # Maxwell 2 on the USB cable
PID_DONGLE = 0x4BA9       # Maxwell USB dongle (wireless)
PRODUCTS = {PID_HEADSET: "Maxwell 2 (USB cable)", PID_DONGLE: "Maxwell 2 (USB dongle)"}

REPORT_ENABLE = 0x06
REPORT_QUAT = 0x0A
SENSOR_RATE = 0x3C


def _allow_shared_access():
    """Open devices non-exclusively, so the Dolby Atmos Renderer plugin and
    Max2OSC can both track at once. hidapi defaults to exclusive on macOS."""
    if sys.platform != "darwin":
        return
    try:
        ctypes.CDLL(hid.__file__).hid_darwin_set_open_exclusive(0)
    except (OSError, AttributeError):
        pass


_allow_shared_access()


def find():
    """-> list of (product_id, path) for every Maxwell head-tracking interface."""
    found = []
    for info in hid.enumerate(VENDOR_ID, 0):
        if (info.get("product_id") in PRODUCTS
                and info.get("usage_page") == VENDOR_USAGE_PAGE
                and info.get("usage") == VENDOR_USAGE):
            found.append((info["product_id"], info["path"]))
    # Prefer the cable when both are present: it is the more direct link.
    found.sort(key=lambda d: d[0] != PID_HEADSET)
    return found


def enable_report(product_id, on=True):
    route = 0x80 if product_id == PID_DONGLE else 0x00
    report = bytes([REPORT_ENABLE, 0x09, route, 0x05, 0x5A, 0x05, 0x00, 0x00,
                    0x09, 0x2E, 0x00, SENSOR_RATE | (0x80 if on else 0x00)])
    return report.ljust(62, b"\x00")


def decode(report):
    """Input report -> (w, x, y, z) unit quaternion, or None if it is not one."""
    if len(report) < 17 or report[0] != REPORT_QUAT:
        return None
    w, x, y, z = struct.unpack_from("<4f", bytes(report[1:17]))
    n = (w * w + x * x + y * y + z * z) ** 0.5
    if not 0.5 < n < 1.5:
        return None
    return w / n, x / n, y / n, z / n


class Maxwell:
    """One open head-tracking interface."""

    def __init__(self, product_id, path):
        self.product_id = product_id
        self.name = PRODUCTS[product_id]
        self._dev = hid.device()
        self._dev.open_path(path)
        self.manufacturer = self._string(self._dev.get_manufacturer_string)
        self.product = self._string(self._dev.get_product_string)

    @staticmethod
    def _string(getter):
        try:
            return (getter() or "").strip()
        except Exception:
            return ""

    def enable(self, on=True):
        self._dev.write(enable_report(self.product_id, on))

    def read(self, timeout_ms):
        """-> quaternion, or None on timeout or a non-orientation report.
        Raises OSError if the device has gone away."""
        data = self._dev.read(64, timeout_ms)
        return decode(data) if data else None

    def close(self, disable=False):
        try:
            if disable:
                self.enable(False)
        except Exception:
            pass
        try:
            self._dev.close()
        except Exception:
            pass
