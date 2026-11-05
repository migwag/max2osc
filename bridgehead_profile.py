"""
Supperware Bridgehead profile parsing.

Implements the profile.txt format documented at
https://supperware.net/headtracker/profile-txt/ so the same file can drive
both Bridgehead and this app.

A profile is four or more non-comment lines, profiles separated by a blank
line. Lines starting with '#' are comments and are ignored anywhere.

    Name shown in the menu
    /[yaw,pitch,roll]
    yaw:1,pitch:1,roll:1
    local 8000

Line 2 is the OSC address: bracketed, as above, sends one message per
component (/yaw, /pitch, /roll); unbracketed (/ypr) sends a single composite
message carrying all the values. Line 3 is the parameter list. Line 4 and any
further lines are destinations; data goes to all of them.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Values a parameter can draw on, filled in by the caller per packet.
ANGLE_FIELDS = ("yaw", "pitch", "roll")
QUAT_FIELDS = ("qw", "qx", "qy", "qz")

DEG = 180.0 / math.pi


class ProfileError(Exception):
    pass


# --------------------------------------------------------------- parameters
@dataclass
class Param:
    """One entry in a parameter list, e.g. '-yaw__:1' or 's:hello' or '0'."""
    kind: str                 # "angle" | "quat" | "zero" | "string"
    name: str = ""            # yaw/pitch/roll, or qw/qx/qy/qz
    negate: bool = False
    quarter_turns: int = 0    # trailing underscores, 90 degrees each
    scale: str = "deg"        # "deg" | "rad" | "unit" (-1..1) | "zero_one"
    text: str = ""            # for kind == "string"

    def render(self, values):
        """Produce this parameter's outgoing value."""
        if self.kind == "string":
            return self.text
        if self.kind == "zero":
            return 0.0
        if self.kind == "quat":
            v = values[self.name]
            return -v if self.negate else v

        radians = values[self.name]                      # angles arrive in radians
        if self.negate:
            radians = -radians
        if self.quarter_turns:
            radians += self.quarter_turns * (math.pi / 2)
        radians = (radians + math.pi) % (2 * math.pi) - math.pi

        if self.scale == "rad":
            return radians
        if self.scale == "unit":
            return radians / math.pi                      # -1..1
        if self.scale == "zero_one":
            return (radians / math.pi + 1.0) / 2.0        # 0..1
        return radians * DEG


_PARAM_RE = re.compile(
    r"""^(?P<sign>[+-])?
         (?P<name>[A-Za-z]+)
         (?P<under1>_*)
         (?::(?P<suffix>[A-Za-z01]))?
         (?P<under2>_*)$""",
    re.VERBOSE,
)

_SUFFIX_SCALE = {"r": "rad", "0": "zero_one", "1": "unit"}


def parse_param(token: str) -> Param:
    token = token.strip()
    if not token:
        raise ProfileError("empty parameter")
    if token == "0":
        return Param(kind="zero")
    if token.startswith("s:"):
        return Param(kind="string", text=token[2:])

    m = _PARAM_RE.match(token)
    if not m:
        raise ProfileError(f"cannot read parameter {token!r}")
    name = m.group("name").lower()
    negate = m.group("sign") == "-"
    turns = len(m.group("under1")) + len(m.group("under2"))
    suffix = m.group("suffix")

    if name in QUAT_FIELDS:
        if suffix:
            raise ProfileError(f"{name} is a quaternion component; "
                               f"':{suffix}' does not apply to it")
        if turns:
            raise ProfileError(f"{name} is a quaternion component; "
                               "trailing underscores do not apply to it")
        return Param(kind="quat", name=name, negate=negate)

    if name not in ANGLE_FIELDS:
        raise ProfileError(f"unknown parameter {token!r}")
    if suffix and suffix.lower() not in _SUFFIX_SCALE:
        raise ProfileError(f"unknown suffix ':{suffix}' on {name}")
    return Param(kind="angle", name=name, negate=negate, quarter_turns=turns,
                 scale=_SUFFIX_SCALE[suffix.lower()] if suffix else "deg")


def parse_params(line: str):
    tokens = [t for t in (p.strip() for p in line.split(",")) if t]
    if not tokens:
        raise ProfileError("empty parameter list")
    params = [parse_param(t) for t in tokens]
    kinds = {p.kind for p in params}
    if "angle" in kinds and "quat" in kinds:
        raise ProfileError("use either angle or quaternion parameters, not both")
    return params


# --------------------------------------------------------------- addresses
_BRACKET_RE = re.compile(r"^(?P<before>[^\[\]]*)\[(?P<names>[^\[\]]*)\](?P<after>[^\[\]]*)$")


def parse_address(line: str):
    """Return (addresses, composite).

    Bracketed forms expand to one address per name and composite is False;
    a plain address yields a single address with composite True.
    """
    line = line.strip()
    if not line.startswith("/"):
        raise ProfileError(f"OSC address must begin with '/': {line!r}")
    m = _BRACKET_RE.match(line)
    if not m:
        if "[" in line or "]" in line:
            raise ProfileError(f"malformed brackets in address {line!r}")
        return [line], True
    names = [n.strip() for n in m.group("names").split(",") if n.strip()]
    if not names:
        raise ProfileError(f"empty bracket list in address {line!r}")
    before, after = m.group("before"), m.group("after")
    return [f"{before}{n}{after}" for n in names], False


# ------------------------------------------------------------ destinations
def parse_destination(line: str):
    """'local 8000' | '8000' | '10.0.0.5 9000' -> (host, port)."""
    parts = line.split()
    if not parts:
        raise ProfileError("empty destination")
    if len(parts) == 1:
        host, port_text = "127.0.0.1", parts[0]
    else:
        host, port_text = " ".join(parts[:-1]), parts[-1]
    if host.lower() in ("local", "localhost"):
        host = "127.0.0.1"
    try:
        port = int(port_text)
    except ValueError:
        raise ProfileError(f"{port_text!r} is not a port number")
    if not 1 <= port <= 65535:
        raise ProfileError(f"port {port} is out of range")
    return host, port


# ---------------------------------------------------------------- profiles
@dataclass
class Profile:
    name: str
    addresses: list
    composite: bool
    params: list
    destinations: list = field(default_factory=list)
    raw_address: str = ""
    raw_params: str = ""

    def messages(self, values):
        """-> [(address, [args])] for one packet's worth of values."""
        rendered = [p.render(values) for p in self.params]
        if self.composite:
            return [(self.addresses[0], rendered)]
        return [(addr, [val]) for addr, val in zip(self.addresses, rendered)]


def parse_profile_block(lines, name_hint=""):
    if len(lines) < 3:
        raise ProfileError("a profile needs a name, an address and a "
                           "parameter list")
    name = lines[0].strip() or name_hint
    addresses, composite = parse_address(lines[1])
    params = parse_params(lines[2])

    if not composite and len(addresses) != len(params):
        raise ProfileError(
            f"{len(addresses)} addresses but {len(params)} parameters -- "
            "a bracketed address needs one parameter per name")

    destinations = []
    for line in lines[3:]:
        destinations.append(parse_destination(line))
    return Profile(name=name, addresses=addresses, composite=composite,
                   params=params, destinations=destinations,
                   raw_address=lines[1].strip(), raw_params=lines[2].strip())


def parse_profiles(text):
    """Parse a whole profile.txt. Returns (profiles, errors).

    Errors are collected rather than raised, so one bad profile does not hide
    the rest -- Bridgehead writes these to ProfileErrors.txt.
    """
    profiles, errors, extras = [], [], {}
    block, block_start = [], 0

    def flush():
        if not block:
            return
        try:
            profiles.append(parse_profile_block(block))
        except ProfileError as exc:
            errors.append(f"line {block_start}: {exc}")
        block.clear()

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            flush()
            continue
        low = line.lower()
        if low.startswith("rx=") or low.startswith("raw="):
            key, _, value = line.partition("=")
            extras.setdefault(key.strip().lower(), []).append(value.strip())
            continue
        if not block:
            block_start = number
        block.append(line)
    flush()
    return profiles, errors, extras
