# Max2OSC

Head tracking from the **Audeze Maxwell 2** to **OSC**, on macOS.

The Maxwell 2 has a head tracker built in. This app reads it over USB --
with the headset on its cable or through the Maxwell USB dongle -- and sends
the orientation as OSC, so binaural and immersive tools can follow your head.

It is the Maxwell counterpart of [NXOSC Reborn](https://github.com/migwag/nx-bridge)
for the Waves Nx tracker: the same window, the same Supperware Bridgehead
profiles, smoothing and send rate, and **the same OSC output for the same
head pose**. A `profiles.txt` written for Bridgehead or NXOSC Reborn works
here unchanged.

Unofficial and not affiliated with Audeze. The headset's head-tracking
protocol was reverse engineered for interoperability; it is documented in the
[maxwell2-ht](https://github.com/migwag/maxwell2-ht) project, which also
provides a Dolby Atmos Renderer plugin. Both can track at the same time.

---

## Using it

Connect the headset by cable or plug in the dongle, and start Max2OSC. It
connects by itself -- there is no pairing -- and reconnects if you unplug,
switch between cable and dongle, or the headset sleeps.

The **Tracker** tab shows the device, how it is connected, the live
orientation, and the stream rate. **Reset Center** makes your current head
position the new centre.

The menu bar icon keeps the app running with the window closed; it offers
**Show Window**, **Reset Center** and **Quit**.

### Sending to your plugin

The **OSC** tab decides what gets sent. Pick a profile per plugin:

| Profile | For |
|---|---|
| SceneRotator | IEM-style `/SceneRotator/yaw,pitch,roll` |
| APL Virtuoso | `/Virtuoso/quat` |
| Audio Brewers | `/[yaw,pitch,roll]`, 0 to 1 |
| Nuendo (HeadPose) | `/head_pose`, seven floats |
| Yaw/Pitch/Roll, Quaternion, ... | generic starting points |

or edit the address, parameters and destinations directly. The syntax is
[Supperware's](https://supperware.net/headtracker/profile-txt/). **Show File**
opens your own `profiles.txt`; **Reload** re-reads it. An `rx=PORT` line
opens an inbound port where `/zero` resets centre and `/connect` 0/1
disconnects or connects.

Directions match NXOSC Reborn, which was tuned against the Bridgehead app:
**yaw** positive turning right, **pitch** positive tipping up, **roll**
positive tipping toward the right shoulder. (Bridgehead's written
documentation calls a left turn positive yaw; the app behaves as above.)
`tools/check_convention.py` measures this with spoken prompts. If a plugin
moves the wrong way, put a `-` in front of that parameter.

**Smoothing** low-passes the orientation (40-80 ms is a good start).
**Send rate** thins the output for receivers that cannot keep up; `0` sends
every packet, about 60 Hz.

---

## Running from source

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python max2osc.py
```

Needs Python 3.10 or newer with a working Tkinter (the python.org installer
has one).

## Building the app

```
./build_app.sh            # -> dist/Max2OSC.app
```

The bundle is ad-hoc signed: fine on the Mac that built it; elsewhere it
needs right-click > Open the first time.

`tools/selftest.py` checks the whole path without the GUI -- headset, worker,
profile, OSC -- including send-rate thinning and Reset Center. It needs the
headset connected.

## How it relates to NXOSC Reborn

| File | Origin |
|---|---|
| `max2osc.py` | NXOSC Reborn's GUI, with the Bluetooth worker replaced by a HID reader thread |
| `maxwell.py` | Maxwell HID protocol: find, enable, decode |
| `orientation.py` | Maxwell-to-Nx axis mapping, plus NXOSC Reborn's angle, Bridgehead and smoothing code |
| `bridgehead_profile.py` | NXOSC Reborn's `nx_profile.py`, unchanged |
| `macos_menubar.py`, `make_icon_art.py`, `make_icons.sh` | from NXOSC Reborn |

The Maxwell reports a quaternion with x forward, y left, z up.
`orientation.maxwell_to_nx` re-expresses it in the frame NXOSC Reborn decodes
the Nx into, and from there the conversion is NXOSC Reborn's own, so both
apps agree -- angles and quaternion alike. The mapping was solved by checking
every signed axis permutation against NXOSC Reborn's code for known right,
up and right-shoulder rotations; exactly one fits, and it was confirmed by
comparing the two apps side by side.

Two things matter on macOS. hidapi opens devices exclusively by default,
which would lock the Dolby Atmos Renderer plugin out, so `maxwell.py` turns
that off. And through the dongle, commands carry a routing flag (`0x80`) or
the headset ignores them.

Not yet tried on Windows. hidapi and Tkinter both work there, so it may well
run from source, but the HID behaviour has only been verified on macOS.
