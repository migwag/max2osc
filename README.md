# Max2OSC

Head tracking from the **Audeze Maxwell 2 ANC** to **OSC**, on macOS -- and,
not yet tested, on Windows.

The Maxwell 2 ANC has a head tracker built in. This app reads it over USB --
with the headset on its cable or through the Maxwell USB dongle -- and sends
the orientation as OSC, so binaural and immersive tools can follow your head.

It is the Maxwell counterpart of [NXOSC Reborn](https://github.com/migwag/nx-bridge)
for the Waves Nx tracker: the same window, the same Supperware Bridgehead
profiles, smoothing and send rate, and **the same OSC output for the same
head pose**. A `profiles.txt` written for Bridgehead or NXOSC Reborn works
here unchanged.

The headset's head-tracking protocol is documented in the
[maxwell2-ht](https://github.com/migwag/maxwell2-ht) project, which also
provides a Dolby Atmos Renderer plugin. Both can track at the same time.

## Disclaimer

This is an independent, unofficial project. It is **not affiliated with,
endorsed by, or supported by Audeze LLC**. "Audeze", "Maxwell" and
"Maxwell 2 ANC" are used only to identify the hardware this app works with.
The headset's head-tracking protocol was reverse engineered for
interoperability. Use it at your own risk.

> **Windows has not been tested yet.** Max2OSC is developed and tested on
> macOS. The code and build are set up for Windows and the Windows build
> passes the automated checks, but it has never been run with a Maxwell 2 ANC
> on a Windows PC. Expect rough edges, and please report what happens.

---

## Using it

Connect the headset by cable or plug in the dongle, and start Max2OSC. It
connects by itself -- there is no pairing -- and reconnects if you unplug,
switch between cable and dongle, or the headset sleeps.

The dongle is the preferred connection: it delivers fresh orientation data
more consistently, while the cable connection repeats stale samples more
often, which makes tracking less smooth.

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

macOS:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python max2osc.py
```

Windows (untested):

```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python max2osc.py
```

Needs Python 3.10 or newer with a working Tkinter (the python.org installers
have one). `tools/check_offline.py` runs the checks that need no headset.

## Building the app

PyInstaller builds for the platform it runs on, so build each on its own OS.

```
./build_app.sh            # macOS   -> dist/Max2OSC.app
build_app.bat             # Windows -> dist\Max2OSC\Max2OSC.exe   (untested)
```

The macOS bundle is ad-hoc signed: fine on the Mac that built it; elsewhere
it needs right-click > Open the first time. On Windows, keep `Max2OSC.exe`
together with the `_internal` folder beside it; SmartScreen may warn on first
run (**More info > Run anyway**).

Without a Windows PC to build on, use GitHub Actions instead
(`.github/workflows/build.yml`): every push builds both platforms, runs the
offline checks on each, and keeps the zips as workflow artifacts. Pushing a
tag such as `v0.1.0` publishes them as a pre-release.

`tools/selftest.py` checks the whole path without the GUI -- headset, worker,
profile, OSC -- including send-rate thinning and Reset Center. It needs the
headset connected.

## Building with Claude Code

If you would rather not run the commands yourself,
[Claude Code](https://claude.com/claude-code) can set up the environment,
build the app and check it for you. It reads this README and the source,
runs the steps, and asks before it changes anything outside the project
folder.

1. **Prerequisites.** A Mac, Python 3.10 or newer from
   [python.org](https://www.python.org/downloads/macos/) (its Tkinter works
   once bundled), and Claude Code (the `claude` command in Terminal, or the
   Code tab in the Claude desktop app).
2. **Get the code.**
   ```
   git clone https://github.com/migwag/max2osc.git
   cd max2osc
   ```
3. **Start Claude Code in that folder** (`claude` in Terminal, or choose the
   folder in the desktop app) and ask, for example:

   > Create the virtual environment, build Max2OSC.app with build_app.sh,
   > run tools/selftest.py against my headset, and copy the app to
   > /Applications.

   Connect the headset by cable or plug in the dongle first, so the self-test
   can find it.
4. **Open Max2OSC** from Applications. It connects to the headset by itself.

Other useful requests:

- *"Run tools/check_convention.py so I can check the directions with my
  head."* It speaks the prompts.
- *"Add a profile for <plugin> that listens on port <n>."* Profiles follow
  [Supperware's format](https://supperware.net/headtracker/profile-txt/).
- *"The app starts and disappears -- find out why."* A bundled app hides its
  errors; Claude Code can run the binary inside it to see them.

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

On Windows, HID devices are shared between programs by default, so there is
nothing to turn off; the macOS-only parts (the menu bar icon, exclusive-open
switch) do nothing there, and closing the window quits the app. What still
needs a real Windows test: that the headset's vendor interface (usage page
`0xFF13`) is found, that the enable command is accepted, and that Audeze's own
software does not hold the headset. Run `tools/selftest.py` and
`tools/check_convention.py` first.

## License

MIT -- see [LICENSE](LICENSE).
