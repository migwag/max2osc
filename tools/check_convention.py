#!/usr/bin/env python3
"""
Check Max2OSC's yaw/pitch/roll signs against Bridgehead's documented convention.

Bridgehead specifies (https://supperware.net/headtracker/profile-txt/):

    yaw    anticlockwise positive   -- turning your head LEFT
    pitch  upward positive          -- tipping your head UP
    roll   clockwise positive       -- tipping your head toward your RIGHT
                                       shoulder, seen from behind

Measures the values profiles send (not the Tracker tab readouts) for one
gesture per axis. It speaks the prompts, so you need not watch the screen.

    .venv/bin/python tools/check_convention.py
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import maxwell as M
import orientation as P

HOLD = 2.5
SETTLE = 1.5

GESTURES = [
    ("yaw", "Turn your head to the LEFT and hold"),
    ("pitch", "Tip your head UP and hold"),
    ("roll", "Tip your head toward your RIGHT shoulder and hold"),
]


def speak(text):
    print(f"\n>>> {text}", flush=True)
    if sys.platform == "darwin":
        subprocess.run(["say", text], check=False)


def capture(device, seconds):
    samples, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        q = device.read(50)
        if q is not None:
            samples.append(q)
    return samples


def main():
    found = M.find()
    if not found:
        sys.exit("No Maxwell found -- connect the cable or dongle.")
    device = M.Maxwell(*found[0])
    device.enable(True)
    print(f"Using {device.name}. Wear the headset as usual.")

    results = {}
    for axis, prompt in GESTURES:
        speak("Face straight ahead and hold still")
        zero = capture(device, 2.0)[-1]
        speak(prompt)
        capture(device, SETTLE)                      # the movement itself
        held = capture(device, HOLD)
        vals = [P.values_for(q, zero)[1] for q in held]
        means = {k: sum(v[k] for v in vals) / len(vals) * P.DEG
                 for k in ("yaw", "pitch", "roll")}
        results[axis] = means
        print("   measured  " + "   ".join(f"{k} {means[k]:+6.1f}" for k in means))
    speak("Done. Back to centre.")
    device.close()

    print("\nBridgehead expects each gesture to move its own axis positive.")
    ok = True
    for axis, _ in GESTURES:
        m = results[axis]
        biggest = max(m, key=lambda k: abs(m[k]))
        good = biggest == axis and m[axis] > 0
        ok &= good
        print(f"  {axis:5s}: {m[axis]:+6.1f} deg  "
              + ("ok" if good else f"WRONG (largest was {biggest} {m[biggest]:+.1f})"))
    print("\nAll three match Bridgehead." if ok else "\nSome axes do not match.")


if __name__ == "__main__":
    main()
