#!/usr/bin/env python3
"""
End-to-end check without the GUI: Maxwell -> TrackerWorker -> profile -> OSC.

    .venv/bin/python tools/selftest.py

Needs the headset connected (cable or dongle). Listens on UDP 9800, runs the
real worker against two profiles, and checks the stream, the send-rate
thinning and Reset Center.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

import bridgehead_profile as NP
import max2osc

PORT = 9800
received = []
lock = threading.Lock()


def on_message(address, *args):
    with lock:
        received.append((time.monotonic(), address, args))


def take():
    with lock:
        out = list(received)
        received.clear()
    return out


dispatcher = Dispatcher()
dispatcher.set_default_handler(on_message)
server = ThreadingOSCUDPServer(("127.0.0.1", PORT), dispatcher)
threading.Thread(target=server.serve_forever, daemon=True).start()

worker = max2osc.TrackerWorker({})
worker.set_profile(NP.parse_profile_block(
    ["SceneRotator", "/SceneRotator/[yaw,pitch,roll]", "yaw,pitch,roll", f"local {PORT}"]))
worker.connect()

failures = []
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    kind, payload = worker.events.get(timeout=5)
    print("event:", kind, payload or "")
    if kind == "connected":
        break
else:
    failures.append("never connected")

time.sleep(0.5)
take()
time.sleep(3)
msgs = take()
yaw = [a[0] for _, addr, a in msgs if addr == "/SceneRotator/yaw"]
rate = len(yaw) / 3
print(f"every packet: {len(msgs)} messages, {rate:.1f} Hz per angle, "
      f"addresses {sorted({m[1] for m in msgs})}, last yaw {yaw[-1] if yaw else None:+.2f}")
if not 40 <= rate <= 70:
    failures.append(f"unexpected rate {rate:.1f} Hz")

worker.set_output(0.0, 25.0)
time.sleep(0.5)
take()
time.sleep(3)
rate25 = sum(1 for _, addr, _a in take() if addr == "/SceneRotator/yaw") / 3
print(f"send rate 25: {rate25:.1f} Hz")
if not 22 <= rate25 <= 27:
    failures.append(f"send rate 25 gave {rate25:.1f} Hz")
worker.set_output(0.0, 0.0)

worker.set_profile(NP.parse_profile_block(
    ["Quaternion wxyz", "/quaternion", "qw,qx,qy,qz", f"local {PORT}"]))
worker.recenter()
time.sleep(0.5)
q = [a for _, addr, a in take() if addr == "/quaternion"]
print(f"after Reset Center: quaternion {tuple(round(v, 3) for v in q[-1]) if q else None}")
if not q or abs(abs(q[-1][0]) - 1) > 0.05:
    failures.append("Reset Center did not bring the quaternion near identity")

worker.shutdown()
server.shutdown()
print("FAIL: " + "; ".join(failures) if failures else "PASS")
sys.exit(1 if failures else 0)
