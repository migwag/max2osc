#!/usr/bin/env python3
"""
Checks that need no headset, so they run in CI on macOS and Windows.

    python tools/check_offline.py

- the Maxwell-to-Nx mapping gives NXOSC Reborn's directions (yaw positive
  turning right, pitch tipping up, roll toward the right shoulder);
- Reset Center turns the current pose into identity;
- the HID enable command and report decode match the documented protocol;
- every profile in the shipped profiles.txt parses and renders.
"""
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)

import bridgehead_profile as NP
import maxwell as M
import orientation as P

failures = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


def axis_q(axis, degrees):
    """Rotation about the Maxwell frame's x (forward), y (left) or z (up)."""
    half = math.radians(degrees) / 2
    v = [0.0, 0.0, 0.0]
    v[axis] = math.sin(half)
    return (math.cos(half), *v)


identity = (1.0, 0.0, 0.0, 0.0)
for name, q, want in (("yaw", axis_q(2, -30), 30.0),      # turn right
                      ("pitch", axis_q(1, -20), 20.0),    # tip up
                      ("roll", axis_q(0, 25), 25.0)):     # right shoulder
    _values, out = P.values_for(q, identity)
    got = {k: math.degrees(out[k]) for k in ("yaw", "pitch", "roll")}
    others = all(abs(got[k]) < 0.01 for k in got if k != name)
    check(abs(got[name] - want) < 0.01 and others,
          f"{name} gesture -> {name} {got[name]:+.2f} (want {want:+.0f}), others zero")

pose = axis_q(2, 40)
_values, out = P.values_for(pose, pose)
check(abs(out["qw"] - 1) < 1e-9 and all(abs(out[k]) < 1e-9 for k in ("yaw", "pitch", "roll")),
      "Reset Center: zero pose maps to identity")

report = M.enable_report(M.PID_DONGLE, True)
check(len(report) == 62 and report[:12] == bytes.fromhex("06 09 80 05 5A 05 00 00 09 2E 00 BC"),
      "enable report through the dongle carries the 0x80 routing flag")
check(M.enable_report(M.PID_HEADSET, False)[2] == 0x00 and M.enable_report(M.PID_HEADSET, False)[11] == 0x3C,
      "disable report on the cable has no routing flag")

q = M.decode(bytes([0x0A]) + struct.pack("<4f", 0.5, 0.5, 0.5, 0.5))
check(q is not None and all(abs(c - 0.5) < 1e-6 for c in q), "report 0x0A decodes to (w, x, y, z)")
check(M.decode(bytes([0x07]) + bytes(16)) is None, "other reports are ignored")

with open(os.path.join(ROOT, "profiles.txt")) as fh:
    profiles, errors, _extras = NP.parse_profiles(fh.read())
check(not errors, f"profiles.txt parses ({len(profiles)} profiles)")
sample = P.profile_values_from(P.derive(*P.maxwell_to_nx(axis_q(2, -30))))
rendered = 0
for profile in profiles:
    try:
        if profile.messages(sample):
            rendered += 1
    except Exception as exc:
        check(False, f"profile '{profile.name}' renders: {exc}")
check(rendered == len(profiles), f"all {len(profiles)} profiles render messages")

print("\nPASS" if not failures else f"\nFAIL: {len(failures)} check(s)")
sys.exit(1 if failures else 0)
