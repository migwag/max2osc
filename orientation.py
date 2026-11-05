"""
Maxwell 2 ANC orientation -> the values a Bridgehead profile can reference.

The Maxwell 2 ANC reports a unit quaternion (w, x, y, z) in a right-handed frame
with x forward, y left and z up. Max2OSC re-expresses it in the component
frame NXOSC Reborn decodes the Waves Nx tracker into, then runs NXOSC Reborn's
own conversion unchanged. Both apps therefore send identical OSC for the same
head pose, and a profiles.txt behaves the same in either -- and in Supperware's
Bridgehead, which NXOSC Reborn was tuned against in practice:

    yaw    positive turning the head RIGHT
    pitch  positive tipping the head UP
    roll   positive tipping toward the RIGHT shoulder

Bridgehead's written documentation calls a left turn positive yaw; NXOSC
Reborn follows how the Bridgehead app behaves, and so does this app.

euler_mode4/6, derive, bridgehead_quaternion, profile_values_from and Smoother
are copied from NXOSC Reborn's nx_protocol.py; keep them in step with it.
"""
import math

DEG = 180.0 / math.pi


# ------------------------------------------------------------- quaternion math
def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def quat_conj(q):
    w, x, y, z = q
    return (w, -x, -y, -z)


def relative(zero, q):
    """Rotation from the zero pose to q, in the head frame at the zero pose."""
    return quat_mul(quat_conj(zero), q)


def maxwell_to_nx(q):
    """Maxwell (w, x, y, z) -> NXOSC Reborn's (a, b, c, w).

    A proper rotation of the axes, chosen so that NXOSC Reborn's derive() +
    profile_values_from() give positive yaw, pitch and roll for turning
    right, tipping up and tipping toward the right shoulder -- what NXOSC
    Reborn sends with the Nx for the same movements. Of the 48 signed axis
    permutations, this is the only one that does.
    """
    w, x, y, z = q
    return -y, z, -x, w


# ----------------------------------------------- copied from nx_protocol.py
def _threeaxisrot(r11, r12, r21, r31, r32):
    return (
        math.atan2(r31, r32),
        math.asin(max(-1.0, min(1.0, r21))),
        math.atan2(r11, r12),
    )


def euler_mode4(a, b, c, w):
    return _threeaxisrot(
        2.0 * (a * c + w * b),
        w * w - a * a - b * b + c * c,
        2.0 * (a * w - b * c),
        2.0 * (a * b + w * c),
        w * w - a * a + b * b - c * c,
    )


def euler_mode6(a, b, c, w):
    return _threeaxisrot(
        2.0 * (b * w - a * c),
        w * w + a * a - b * b - c * c,
        2.0 * (a * b + w * c),
        2.0 * (a * w - b * c),
        w * w - a * a + b * b - c * c,
    )


def derive(a, b, c, w):
    """Quaternion -> every value we expose. Angles in radians."""
    e4 = euler_mode4(a, b, c, w)
    e6 = euler_mode6(a, b, c, w)
    return {
        "quaternion": (a, b, c, w),
        "xyz":   (e6[0], e6[2], e6[1]),
        "yaw":    e4[2],
        "pitch": -e4[1],
        "roll":  -e4[0],
    }


def bridgehead_quaternion(a, b, c, w):
    """NXOSC Reborn's component frame -> Supperware Bridgehead's."""
    return w, a, -c, b


def profile_values_from(values):
    """Derived values -> the names a Bridgehead profile can reference."""
    a, b, c, w = values["quaternion"]
    qw, qx, qy, qz = bridgehead_quaternion(a, b, c, w)
    return {
        "yaw": -values["yaw"],
        "pitch": -values["pitch"],
        "roll": values["roll"],
        "qw": qw, "qx": qx, "qy": qy, "qz": qz,
    }


class Smoother:
    """Exponential low-pass on the quaternion (normalised lerp).

    `tau` is the time constant in seconds; zero disables smoothing.
    """

    def __init__(self, tau=0.0):
        self.tau = max(0.0, float(tau))
        self._state = None
        self._last = None

    def reset(self):
        self._state = None
        self._last = None

    def push(self, quat, now):
        if self.tau <= 0.0:
            self._state = quat
            self._last = now
            return quat
        if self._state is None or self._last is None or now <= self._last:
            self._state = quat
            self._last = now
            return quat

        dt = now - self._last
        self._last = now
        alpha = 1.0 - math.exp(-dt / self.tau)

        prev = self._state
        # q and -q are the same rotation; without this the filter can take
        # the long way round and lurch.
        if sum(p * c for p, c in zip(prev, quat)) < 0.0:
            quat = tuple(-c for c in quat)

        blended = tuple(p + (c - p) * alpha for p, c in zip(prev, quat))
        norm = math.sqrt(sum(c * c for c in blended))
        if norm < 1e-9:
            self._state = quat
            return quat
        self._state = tuple(c / norm for c in blended)
        return self._state


# --------------------------------------------------------------- one call
def values_for(q, zero):
    """Raw Maxwell quaternion + zero pose -> (derived values, profile values)."""
    a, b, c, w = maxwell_to_nx(relative(zero, q))
    values = derive(a, b, c, w)
    return values, profile_values_from(values)
