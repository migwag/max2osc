#!/usr/bin/env python3
"""
Max2OSC -- Audeze Maxwell 2 ANC head tracking to OSC, with a GUI.

The counterpart of NXOSC Reborn for the Maxwell 2 ANC: the same Tracker and OSC
tabs, the same Supperware Bridgehead profiles, smoothing and send rate, and
the same OSC output for the same head pose. Only the tracker side differs --
the headset is read over USB HID, by cable or through the Maxwell dongle,
and connects by itself.

    python max2osc.py

Tkinter is in the standard library, so there is no GUI dependency to install.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

import bridgehead_profile as NP
import macos_menubar as MB
import maxwell as M
import orientation as P

APP_NAME = "Max2OSC"           # internal: config directory
APP_TITLE = "Max2OSC"          # what the user sees
UI_HZ = 20          # readout refresh; the OSC stream runs at the headset's ~60 Hz


# --------------------------------------------------------------------- config
def resource_path(name: str) -> str:
    """Locate a bundled file, frozen (PyInstaller) or running from source."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def set_window_icon(root: tk.Tk) -> None:
    """Give the window our icon where the platform uses one.

    macOS takes the Dock icon from the app bundle instead, so this is mainly
    for Windows and Linux. Any failure is cosmetic -- never fatal.
    """
    path = resource_path("icon_256.png")
    if not os.path.exists(path):
        return
    try:
        root._icon_image = tk.PhotoImage(file=path)   # keep a reference alive
        root.iconphoto(True, root._icon_image)
    except Exception:
        pass


def config_path() -> str:
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base, APP_NAME, "config.json")


DEFAULT_PROFILE_NAME = "SceneRotator (degrees)"

DEFAULT_CONFIG = {
    # The active profile is always built from these three fields; the
    # drop-down simply fills them in. Editing them yields a one-off profile.
    "profile_name": DEFAULT_PROFILE_NAME,
    "address": "/SceneRotator/[yaw,pitch,roll]",
    "params": "yaw,pitch,roll",
    "destinations": "local 8000",
    "smoothing": False,
    "smoothing_ms": 60.0,
    "send_rate": 0.0,          # 0 = send every packet (~60 Hz)
    "auto_connect": True,      # connect at launch, as the Maxwell needs no pairing
}


def profiles_path() -> str:
    return os.path.join(os.path.dirname(config_path()), "profiles.txt")


def ensure_profiles_file() -> str:
    """Seed the user's profiles.txt from the one we ship, once."""
    path = profiles_path()
    if os.path.exists(path):
        return path
    shipped = resource_path("profiles.txt")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(shipped) as src, open(path, "w") as dst:
            dst.write(src.read())
    except OSError:
        pass
    return path


def load_profiles():
    """-> (profiles, errors, extras). Falls back to the shipped file if need be.

    `extras` carries the file-level lines Bridgehead allows anywhere in the
    file, notably rx=PORT for the inbound control channel.
    """
    for path in (ensure_profiles_file(), resource_path("profiles.txt")):
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            continue
        profiles, errors, extras = NP.parse_profiles(text)
        if profiles or errors:
            return profiles, errors, extras
    return [], [], {}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(config_path()) as fh:
            stored = json.load(fh)
        for key, value in stored.items():
            if key in cfg:            # unknown keys from older versions lapse
                cfg[key] = value
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    path = config_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(cfg, fh, indent=2)
    except OSError:
        pass  # a config we cannot persist is not worth crashing over


class ControlListener:
    """Bridgehead's inbound control channel, enabled by an rx=PORT line.

    Runs a UDP OSC server on its own thread and hands each recognised message
    to `on_message` as a (kind, argument) pair. Nothing is acted on here: the
    GUI drains them on the main thread, so inbound OSC takes exactly the same
    path as a button press.
    """

    def __init__(self, port, on_message):
        self.port = int(port)
        self.ok = False
        self._server = None
        dispatcher = Dispatcher()
        dispatcher.map("/zero", lambda _a, *args: on_message("zero", None))
        dispatcher.map("/connect",
                       lambda _a, *args: on_message("connect",
                                                    args[0] if args else None))
        dispatcher.set_default_handler(lambda _a, *_args: None)
        try:
            self._server = ThreadingOSCUDPServer(("0.0.0.0", self.port), dispatcher)
        except Exception:
            return
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        self.ok = True

    def stop(self):
        if self._server is not None:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
        self._server = None
        self.ok = False


# --------------------------------------------------------------------- worker
class TrackerWorker:
    """Reads the Maxwell on its own thread and does all HID and OSC work.

    Never touches widgets. It posts ("event", payload) tuples to `events`,
    which the GUI drains on the main thread, and publishes the newest derived
    frame to `latest` (a plain attribute -- reference assignment is atomic).

    While connection is wanted it keeps looking for the headset, so plugging
    in, switching between cable and dongle, or waking the headset reconnects
    without a click.
    """

    POLL_S = 1.0          # how often to look for the headset while absent
    QUIET_S = 1.0         # re-send enable after this long without data

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.events: queue.Queue = queue.Queue()
        self.latest = None
        self.frames = 0
        self.profile = None
        self._clients: list = []
        self.smoother = P.Smoother(0.0)
        self.min_interval = 0.0      # 0 means send every packet
        self.sent = 0
        self._next_send = None
        self._zero = None            # raw quaternion that counts as centre
        self._zero_requested = True
        self._want = threading.Event()
        self._quit = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _post(self, kind, payload=None):
        self.events.put((kind, payload))

    def set_output(self, smoothing_tau, send_rate):
        """Smoothing time constant in seconds, send rate in Hz (0 = every packet)."""
        if smoothing_tau != self.smoother.tau:
            self.smoother.tau = max(0.0, smoothing_tau)
            self.smoother.reset()
        interval = (1.0 / send_rate) if send_rate and send_rate > 0 else 0.0
        if interval != self.min_interval:
            self._next_send = None          # restart the schedule cleanly
        self.min_interval = interval

    def set_profile(self, profile):
        """Swap in a parsed profile and open a socket per destination."""
        clients = []
        for host, port in profile.destinations:
            try:
                clients.append(SimpleUDPClient(host, port))
            except Exception as exc:
                self._post("error", f"cannot send to {host}:{port} -- {exc}")
        self.profile = profile
        self._clients = clients

    # -- public, callable from the GUI thread ---------------------------
    def connect(self):
        self._want.set()

    def disconnect(self):
        self._want.clear()

    def recenter(self):
        self._zero_requested = True
        self._post("recentered")

    def shutdown(self):
        self._want.clear()
        self._quit.set()
        self._thread.join(timeout=2.0)

    # -- reader thread --------------------------------------------------
    def _run(self):
        searching_reported = False
        while not self._quit.is_set():
            if not self._want.wait(timeout=0.2):
                continue
            found = []
            try:
                found = M.find()
            except Exception as exc:
                self._post("error", f"HID enumeration failed: {exc}")
            if not found:
                if not searching_reported:
                    self._post("searching")
                    searching_reported = True
                self._quit.wait(self.POLL_S)
                continue
            searching_reported = False
            product_id, path = found[0]
            try:
                device = M.Maxwell(product_id, path)
            except Exception as exc:
                self._post("error", f"could not open the Maxwell: {exc}")
                self._quit.wait(self.POLL_S)
                continue
            self._stream(device)

    def _stream(self, device):
        self.frames = 0
        self.sent = 0
        self._next_send = None
        self._zero_requested = True
        self.smoother.reset()
        self._post("connected", {"name": device.name,
                                 "product": device.product,
                                 "manufacturer": device.manufacturer})
        dropped = False
        last_data = last_kick = 0.0
        try:
            while self._want.is_set() and not self._quit.is_set():
                now = time.monotonic()
                if now - last_data > self.QUIET_S and now - last_kick > self.QUIET_S:
                    device.enable(True)
                    last_kick = now
                quat = device.read(100)
                if quat is not None:
                    last_data = time.monotonic()
                    self._on_quaternion(quat, last_data)
        except OSError:
            dropped = True
        finally:
            # Leave the stream running: the Dolby Atmos Renderer plugin may be
            # reading the same headset, and stopping it would stall that.
            device.close(disable=False)
            self.latest = None
        self._post("dropped" if dropped else "disconnected")

    def _on_quaternion(self, quat, now):
        if self._zero_requested or self._zero is None:
            self._zero = quat
            self._zero_requested = False
            self.smoother.reset()
        try:
            nx = P.maxwell_to_nx(P.relative(self._zero, quat))
            if self.smoother.tau > 0.0:
                nx = self.smoother.push(nx, now)
            values = P.derive(*nx)
        except Exception:
            return

        self.frames += 1
        self.latest = values

        # The filter still sees every packet; only sending is thinned, so
        # reducing the rate does not turn smoothing into aliasing. The
        # schedule accumulates rather than measuring from the last send, so
        # late packets do not drag the rate down; the epsilon absorbs jitter.
        if self.min_interval:
            if self._next_send is None:
                self._next_send = now
            if now + 1e-6 < self._next_send:
                return
            self._next_send = max(now, self._next_send + self.min_interval)

        profile = self.profile
        if profile is None:
            return
        try:
            outgoing = profile.messages(P.profile_values_from(values))
        except Exception:
            return
        for client in self._clients:
            try:
                for address, args in outgoing:
                    client.send_message(address, args)
            except Exception:
                pass
        self.sent += 1


# --------------------------------------------------------------------- GUI
class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=12)
        self.root = root
        self.cfg = load_config()
        self.worker = TrackerWorker(self.cfg)
        self.connected = False
        self.wanted = False
        self.busy = False
        self._last_frames = 0
        self._last_time = time.monotonic()
        self._rate = 0.0
        self._last_sent = 0
        self._out_rate = 0.0
        self._dest_summary = ""
        self._stream_start = time.monotonic()
        self._menubar_text = ""
        self.control = None
        self._control_note = ""
        self._control_error = ""

        root.title(APP_TITLE)
        root.resizable(False, False)
        self.grid(sticky="nsew")

        self._build()
        self._build_menubar()
        # Activate the stored profile so the worker can send from the first
        # packet, without waiting for the user to visit the OSC tab.
        self._on_apply()
        self._note("not connected")
        self._menubar_status("Not connected")
        if self.cfg.get("auto_connect", True):
            self._on_connect()
        self._tick()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- menu bar (macOS) ------------------------------------------------
    def _build_menubar(self):
        """A status bar extra, so the app can run with no window.

        Only on macOS; elsewhere closing the window still quits, which is
        what those platforms expect.
        """
        self.menubar = None
        if not MB.available():
            return
        bar = MB.MenuBar(
            title="Max2",
            icon_idle=resource_path("menubar_idle.png"),
            icon_active=resource_path("menubar_active.png"),
            tooltip=APP_TITLE,
            items=[
                ("Not connected", self.show_window),   # 0: doubles as status
                None,
                ("Show Window", self.show_window),
                ("Reset Center", self._on_recenter),
                None,
                ("Quit " + APP_TITLE, self.quit_app),
            ])
        self.menubar = bar if bar.ok else None

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        if self.menubar:
            self.menubar.set_dock_visible(True)
        try:
            self.root.focus_force()
        except Exception:
            pass

    def hide_window(self):
        """Closing the window leaves the app running in the menu bar."""
        save_config(self.cfg)
        self.root.withdraw()
        if self.menubar:
            self.menubar.set_dock_visible(False)

    def quit_app(self):
        save_config(self.cfg)
        if self.control:
            self.control.stop()
        if self.menubar:
            self.menubar.remove()
        self.worker.shutdown()
        self.root.destroy()

    # -- construction ---------------------------------------------------
    def _build(self):
        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew")
        tracker = ttk.Frame(notebook, padding=12)
        osc = ttk.Frame(notebook, padding=12)
        notebook.add(tracker, text="Tracker")
        notebook.add(osc, text="OSC")
        self._build_tracker(tracker)
        self._build_osc(osc)

        self.status = ttk.Label(self, text="not connected", anchor="w")
        self.status.grid(row=1, column=0, sticky="ew", pady=(8, 0))

    def _build_tracker(self, parent):
        parent.columnconfigure(1, weight=1)
        row = 0

        self.connect_button = ttk.Button(parent, text="Connect",
                                         command=self._on_connect)
        self.connect_button.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1

        self.spinner = ttk.Progressbar(parent, mode="indeterminate", length=120)
        self.spinner.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 10))
        row += 1

        self.device_var = tk.StringVar(value="No Data")
        self.link_var = tk.StringVar(value="No Data")
        for label, var in (("Device", self.device_var),
                           ("Connection", self.link_var)):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
            ttk.Label(parent, textvariable=var).grid(row=row, column=1, sticky="e")
            row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2,
                                   sticky="ew", pady=10)
        row += 1

        mono = ("Menlo" if sys.platform == "darwin"
                else "Consolas" if os.name == "nt" else "monospace", 11)
        self.readouts = {}
        for key, label in (("quaternion", "Quaternion"), ("xyz", "Euler xyz"),
                           ("yaw", "Yaw"), ("pitch", "Pitch"), ("roll", "Roll")):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
            var = tk.StringVar(value="--")
            ttk.Label(parent, textvariable=var, font=mono).grid(
                row=row, column=1, sticky="e")
            self.readouts[key] = var
            row += 1

        ttk.Separator(parent).grid(row=row, column=0, columnspan=2,
                                   sticky="ew", pady=10)
        row += 1

        self.recenter_button = ttk.Button(parent, text="Reset Center",
                                          command=self._on_recenter,
                                          state="disabled")
        self.recenter_button.grid(row=row, column=0, columnspan=2)

    def _build_osc(self, parent):
        parent.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(parent, text="Profile").grid(row=row, column=0, sticky="w")
        self.profile_var = tk.StringVar(value=self.cfg["profile_name"])
        self.profile_menu = ttk.Combobox(parent, textvariable=self.profile_var,
                                         state="readonly", width=34)
        self.profile_menu.grid(row=row, column=1, sticky="ew", pady=2)
        self.profile_menu.bind("<<ComboboxSelected>>", self._on_profile_chosen)
        row += 1

        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=1, sticky="e", pady=(0, 8))
        ttk.Button(buttons, text="Reload", width=9,
                   command=self._reload_profiles).grid(row=0, column=0, padx=2)
        ttk.Button(buttons, text="Show File", width=11,
                   command=self._show_profiles_file).grid(row=0, column=1, padx=2)
        row += 1

        self.address_var = tk.StringVar(value=self.cfg["address"])
        self.params_var = tk.StringVar(value=self.cfg["params"])
        for label, var in (("OSC address", self.address_var),
                           ("Parameters", self.params_var)):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w")
            entry = ttk.Entry(parent, textvariable=var, width=36)
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            entry.bind("<Return>", lambda _e: self._on_apply())
            row += 1

        ttk.Label(parent, text="Destinations").grid(row=row, column=0, sticky="nw")
        self.dest_text = tk.Text(parent, width=36, height=3, wrap="none",
                                 font=ttk.Style().lookup("TEntry", "font"))
        self.dest_text.grid(row=row, column=1, sticky="ew", pady=2)
        self.dest_text.insert("1.0", self.cfg["destinations"])
        row += 1

        output = ttk.Frame(parent)
        output.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.smooth_var = tk.BooleanVar(value=bool(self.cfg["smoothing"]))
        ttk.Checkbutton(output, text="Smoothing", variable=self.smooth_var,
                        command=self._apply_output).grid(row=0, column=0,
                                                         sticky="w")
        self.smooth_ms_var = tk.StringVar(value=f"{self.cfg['smoothing_ms']:g}")
        e = ttk.Entry(output, textvariable=self.smooth_ms_var, width=6)
        e.grid(row=0, column=1, padx=(6, 2))
        e.bind("<Return>", lambda _ev: self._apply_output())
        ttk.Label(output, text="ms").grid(row=0, column=2, sticky="w")

        ttk.Label(output, text="Send rate").grid(row=0, column=3, sticky="e",
                                                 padx=(18, 0))
        self.rate_var = tk.StringVar(value=f"{self.cfg['send_rate']:g}")
        e = ttk.Entry(output, textvariable=self.rate_var, width=6)
        e.grid(row=0, column=4, padx=(6, 2))
        e.bind("<Return>", lambda _ev: self._apply_output())
        ttk.Label(output, text="Hz").grid(row=0, column=5, sticky="w")
        row += 1

        ttk.Label(parent, foreground="grey", wraplength=330, justify="left",
                  text=("Send rate 0 means every packet (~60 Hz); set it lower "
                        "for receivers that cannot keep up. Smoothing still "
                        "sees every packet.")
                  ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(2, 0))
        row += 1

        ttk.Label(parent, foreground="grey", wraplength=330, justify="left",
                  text=("/[yaw,pitch,roll] sends separate messages; /ypr sends "
                        "one. Angles are degrees; :r radians, :1 maps to -1..1, "
                        ":0 to 0..1. A leading - reverses, each trailing _ adds "
                        "90\u00b0. One destination per line.")
                  ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
        row += 1

        self.profile_note = ttk.Label(parent, wraplength=330, justify="left",
                                      text="")
        self.profile_note.grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        apply_row = ttk.Frame(parent)
        apply_row.grid(row=row, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(apply_row, text="Restore Defaults",
                   command=self._on_defaults).grid(row=0, column=0, padx=4)
        ttk.Button(apply_row, text="Apply",
                   command=self._on_apply).grid(row=0, column=1)

        self._reload_profiles(announce=False)

    def _apply_output(self):
        """Smoothing and send rate -- the tickbox takes effect at once."""
        def number(var, name, default, lo, hi):
            text = var.get().strip()
            if not text:
                return default
            try:
                value = float(text)
            except ValueError:
                raise ValueError(f"{name} '{text}' is not a number")
            if not lo <= value <= hi:
                raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
            return value

        try:
            ms = number(self.smooth_ms_var, "Smoothing", 60.0, 0.0, 5000.0)
            rate = number(self.rate_var, "Send rate", 0.0, 0.0, 200.0)
        except ValueError as exc:
            self.profile_note.configure(foreground="#b00", text=str(exc))
            return

        self.cfg["smoothing"] = bool(self.smooth_var.get())
        self.cfg["smoothing_ms"] = ms
        self.cfg["send_rate"] = rate
        save_config(self.cfg)
        self.worker.set_output(ms / 1000.0 if self.cfg["smoothing"] else 0.0,
                               rate)
        self.profile_note.configure(foreground="grey", text="")
        self._show_control_error()     # this runs last; do not lose the rx= note
        self._note(("smoothing %gms" % ms if self.cfg["smoothing"]
                    else "smoothing off")
                   + (f", sending at {rate:g} Hz" if rate else ", every packet"))

    # -- profiles -------------------------------------------------------
    def _reload_profiles(self, announce=True):
        self.profiles, errors, extras = load_profiles()
        self._apply_control_listener(extras)
        names = [p.name for p in self.profiles]
        self.profile_menu.configure(values=names)
        if self.cfg["profile_name"] in names:
            self.profile_var.set(self.cfg["profile_name"])
        if errors:
            self.profile_note.configure(
                foreground="#b00", text="profiles.txt: " + "; ".join(errors[:3]))
        else:
            self.profile_note.configure(foreground="grey", text="")
        if announce:
            self._note(f"{len(names)} profiles loaded"
                       + (f", {len(errors)} with errors" if errors else ""))

    def _apply_control_listener(self, extras):
        """Open, move or close the rx= control port as the file dictates."""
        values = (extras or {}).get("rx") or []
        port = None
        if values:
            try:
                port = int(str(values[-1]).strip())
            except ValueError:
                port = None

        current = self.control.port if self.control and self.control.ok else None
        if port == current:
            return
        if self.control:
            self.control.stop()
            self.control = None
        if port is None:
            self._control_note = ""
            self._control_error = ""
            return
        listener = ControlListener(port, self._on_control_message)
        if listener.ok:
            self.control = listener
            self._control_note = f"  |  /zero on {port}"
            self._control_error = ""
        else:
            self.control = None
            self._control_note = ""
            self._control_error = (f"rx={port}: cannot listen on that port — "
                                   "something else is already using it")
        self._show_control_error()

    def _show_control_error(self):
        """Keep the rx= problem visible; Apply clears the note otherwise."""
        if self._control_error:
            self.profile_note.configure(foreground="#b00",
                                        text=self._control_error)

    def _on_control_message(self, kind, argument):
        """Called from the OSC thread; hand off to the GUI thread untouched."""
        self.worker.events.put((f"control:{kind}", argument))

    def _on_profile_chosen(self, _event=None):
        chosen = next((p for p in self.profiles
                       if p.name == self.profile_var.get()), None)
        if chosen is None:
            return
        self.address_var.set(chosen.raw_address)
        self.params_var.set(chosen.raw_params)
        self.dest_text.delete("1.0", "end")
        self.dest_text.insert("1.0", "\n".join(
            f"{h} {p}" for h, p in chosen.destinations) or "local 8000")
        self._on_apply()

    def _show_profiles_file(self):
        path = ensure_profiles_file()
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", path], check=False)
            elif os.name == "nt":
                subprocess.run(["explorer", "/select,", path], check=False)
            else:
                subprocess.run(["xdg-open", os.path.dirname(path)], check=False)
        except Exception:
            messagebox.showinfo(APP_TITLE, f"Profiles file:\n{path}")

    def _current_profile(self):
        """Build a profile from the three fields. Returns (profile, error)."""
        destinations = self.dest_text.get("1.0", "end").strip()
        lines = [self.profile_var.get() or "Custom",
                 self.address_var.get().strip(),
                 self.params_var.get().strip()]
        lines += [d.strip() for d in destinations.splitlines() if d.strip()]
        if len(lines) < 4:
            return None, "Give at least one destination, such as 'local 8000'."
        try:
            return NP.parse_profile_block(lines), None
        except NP.ProfileError as exc:
            return None, str(exc)

    # -- actions --------------------------------------------------------
    def _on_connect(self):
        if self.wanted:
            self.wanted = False
            self.worker.disconnect()
            self.connect_button.configure(text="Connect")
            return
        self.wanted = True
        self.connect_button.configure(text="Disconnect")
        self._set_busy(True, "looking for the Maxwell ...")
        self.worker.connect()

    def _on_recenter(self):
        self.worker.recenter()

    def _on_defaults(self):
        self.profile_var.set(DEFAULT_PROFILE_NAME)
        self.address_var.set(DEFAULT_CONFIG["address"])
        self.params_var.set(DEFAULT_CONFIG["params"])
        self.dest_text.delete("1.0", "end")
        self.dest_text.insert("1.0", DEFAULT_CONFIG["destinations"])
        self._on_apply()

    def _on_apply(self):
        profile, error = self._current_profile()
        if error:
            self.profile_note.configure(foreground="#b00", text=error)
            self._note("profile not applied")
            return

        self.profile_note.configure(foreground="grey", text="")
        self._show_control_error()
        self.cfg["profile_name"] = self.profile_var.get()
        self.cfg["address"] = self.address_var.get().strip()
        self.cfg["params"] = self.params_var.get().strip()
        self.cfg["destinations"] = self.dest_text.get("1.0", "end").strip()
        save_config(self.cfg)
        self._apply_output()
        self.worker.set_profile(profile)

        where = ", ".join(f"{h}:{p}" for h, p in profile.destinations)
        self._dest_summary = where
        self._note(f"{len(profile.addresses)} message"
                   f"{'' if len(profile.addresses) == 1 else 's'} -> {where}"
                   f"{self._control_note}")

    # -- state ----------------------------------------------------------
    def _set_busy(self, busy, note=None):
        # The button stays live while busy: waiting for the headset can last
        # indefinitely, and Disconnect is how you stop it.
        self.busy = busy
        if busy:
            self.spinner.start(12)
        else:
            self.spinner.stop()
        if note:
            self._note(note)

    def _note(self, text):
        self.status.configure(text=text)

    def _menubar_status(self, text):
        """The first menu item doubles as a status line, so keep it short."""
        if getattr(self, "menubar", None) and text != self._menubar_text:
            self._menubar_text = text
            self.menubar.set_item_title(0, text)

    def _clear_readouts(self):
        for var in self.readouts.values():
            var.set("--")
        self.device_var.set("No Data")
        self.link_var.set("No Data")

    # -- event pump -----------------------------------------------------
    def _tick(self):
        try:
            self._refresh()
        except Exception:
            traceback.print_exc()        # keep going; a frozen UI is worse
        finally:
            self.after(int(1000 / UI_HZ), self._tick)

    def _refresh(self):
        while True:
            try:
                kind, payload = self.worker.events.get_nowait()
            except queue.Empty:
                break
            self._handle(kind, payload)

        values = self.worker.latest
        if values is not None:
            a, b, c, w = values["quaternion"]
            self.readouts["quaternion"].set(
                f"{a:+.4f} {b:+.4f} {c:+.4f} {w:+.4f}")
            x, y, z = (v * P.DEG for v in values["xyz"])
            self.readouts["xyz"].set(f"{x:+8.2f} {y:+8.2f} {z:+8.2f}")
            for key in ("yaw", "pitch", "roll"):
                self.readouts[key].set(f"{values[key] * P.DEG:+8.2f}°")

        if self.connected:
            # Both rates are measured over the gap since the last tick and
            # then smoothed. Counting per tick alone would quantise to
            # multiples of UI_HZ and flicker; averaging since the stream
            # began would mean a change to the send rate took most of a
            # session to show up.
            now = time.monotonic()
            elapsed = now - self._last_time
            if elapsed > 0:
                incoming = (self.worker.frames - self._last_frames) / elapsed
                self._rate = (incoming if self._rate == 0.0
                              else self._rate * 0.8 + incoming * 0.2)
                # Smoothed harder than the incoming rate: at a low send
                # rate a tick's window holds zero or one packet, so the raw
                # estimate swings wildly. ~1 s of averaging keeps the number
                # readable while still following a change within a second or
                # two.
                outgoing = (self.worker.sent - self._last_sent) / elapsed
                self._out_rate = (outgoing if self._out_rate == 0.0
                                  else self._out_rate * 0.94 + outgoing * 0.06)
            self._last_frames = self.worker.frames
            self._last_sent = self.worker.sent
            self._last_time = now
            out = ""
            if self.worker.min_interval:
                out = f" / {self._out_rate:.0f} Hz out"
            self._note(f"streaming   {self._rate:.1f} Hz{out}   "
                       f"frames {self.worker.frames}   "
                       f"OSC -> {self._dest_summary}{self._control_note}")
            self._menubar_status(f"Streaming {self._rate:.0f} Hz")
            if self.menubar:
                self.menubar.set_active(True)

    def _handle(self, kind, payload):
        if kind == "searching":
            self._note("waiting for the Maxwell -- switch it on, or connect the "
                       "cable or dongle")
            self._menubar_status("Waiting for Maxwell")
        elif kind == "connected":
            self._set_busy(False)
            self.connected = True
            self._last_frames = self.worker.frames
            self._last_time = time.monotonic()
            self._stream_start = self._last_time
            self._rate = 0.0
            self._last_sent = self.worker.sent
            self._out_rate = 0.0
            self.recenter_button.configure(state="normal")
            self.device_var.set(payload.get("product") or "Audeze Maxwell")
            self.link_var.set("USB dongle" if "dongle" in payload["name"]
                              else "USB cable")
            self._note("connected")
            self._menubar_status("Connected")
        elif kind in ("disconnected", "dropped"):
            self.connected = False
            self.recenter_button.configure(state="disabled")
            self._clear_readouts()
            if self.wanted:
                # The worker keeps looking; say so rather than "not connected".
                self._set_busy(True)
                self._note("connection lost -- waiting for the Maxwell")
                self._menubar_status("Waiting for Maxwell")
            else:
                self._set_busy(False)
                self._note("not connected")
                self._menubar_status("Not connected")
            if self.menubar:
                self.menubar.set_active(False)
        elif kind == "control:zero":
            self._note("reset center (via OSC)")
            self.worker.recenter()
        elif kind == "control:connect":
            want = payload
            if want in (0, 0.0, False) and self.wanted:
                self._on_connect()
            elif want in (1, 1.0, True) and not self.wanted:
                self._on_connect()
            elif want is None:
                self._on_connect()
        elif kind == "recentered":
            self._note("reset center sent")
        elif kind == "error":
            # The worker retries on its own, so keep the spinner while it does.
            self._set_busy(self.wanted and not self.connected)
            self._note(str(payload))

    def _on_close(self):
        if self.menubar:
            self.hide_window()
        else:
            self.quit_app()


def main():
    root = tk.Tk()
    set_window_icon(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
