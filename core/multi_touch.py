"""Real multi-finger gestures, by writing touch events as root.

Why this needs root
-------------------
Nothing in the normal ADB toolbox produces two pointers at once. ``input
tap``/``swipe``/``motionevent`` each carry a single pointer, and two
parallel ``input swipe`` processes are two INDEPENDENT one-finger drags —
CoC reads them as a camera pan, not a pinch (measured: the base spanned
868px before two such "pinches" and 867px after).

The only route to genuine multi-touch is writing MT protocol B events
straight to the touchscreen's input node. As the plain shell user that is
refused — the node is ``u:object_r:input_device:s0`` and SELinux is
Enforcing — so every gesture here runs through ``su``.

Coordinate mapping
------------------
The driver does not speak screen pixels. It reports ABS_MT_POSITION_X/Y
on its own grid (0..``raw_max``) covering the physical panel, while the
game sees a rotated, resized logical display — on the reference device a
1440x3088 panel presented as a 1350x1080 landscape surface.

Rather than derive that chain analytically (panel -> display override ->
rotation, where a wrong guess silently taps the wrong place), the mapping
is three booleans in config: whether the axes are swapped and whether
each is inverted. That covers all eight orientations, and which one is
right is settled by watching where a touch actually lands — see
``scripts`` note in the config comment.
"""

from __future__ import annotations

import time
from typing import Sequence, Tuple

from core.adb_handler import _run, get_active_resolution
from core.logger import BotLogger
from core.settings import Settings

log = BotLogger.get("multi_touch")

Point = Tuple[int, int]

# Linux input event codes (linux/input-event-codes.h).
_EV_SYN, _SYN_REPORT = 0, 0
_EV_KEY, _BTN_TOUCH = 1, 330
_EV_ABS = 3
_ABS_MT_SLOT, _ABS_MT_TOUCH_MAJOR = 47, 48
_ABS_MT_POSITION_X, _ABS_MT_POSITION_Y = 53, 54
_ABS_MT_TRACKING_ID = 57

# Most panels expose ten slots; more fingers than this would be dropped
# silently by the driver, which reads as "some troops never deployed".
MAX_SLOTS = 10


def _quote(command: str) -> str:
    """Wrap a command so the device's ``su`` receives it as ONE argument.

    ``adb shell`` does not preserve argv: it joins everything with spaces
    and hands the result to the device shell to re-parse. So passing
    ``["su", "-c", "a; b"]`` arrives as ``su -c a; b`` — ``su`` runs only
    ``a`` as root and the device shell runs ``b`` as the shell user. For a
    gesture that means the first sendevent succeeds and every following
    one is denied, leaving a finger pressed down with nothing to lift it.
    """
    return "'" + command.replace("'", """'"'"'""") + "'"


# How a command is escalated to uid 0 on this device. The right form is
# not predictable: emulators usually hand out a root adb shell outright,
# Magisk-style su takes ``-c``, and the toolbox su that ships with many
# Android images takes a uid instead and no ``-c`` at all.
ROOT_MODES: tuple[tuple[str, str], ...] = (
    ("shell", "{cmd}"),                     # adb shell is already root
    ("su -c", "su -c {quoted}"),            # Magisk / classic su
    ("su 0",  "su 0 sh -c {quoted}"),       # toolbox su: su <uid> <argv…>
)

# Which of ROOT_MODES works here, or None when none of them do.
_root_mode: str | None = None
_probed_root = False
# What each mode answered on the last probe, so the UI can show why.
_last_attempts = ""

# Auto-detected touchscreen node and its coordinate ceiling.
_detected: tuple[str, int] | None = None


def enabled() -> bool:
    """The user-facing switch, from the Settings tab."""
    return bool(Settings().get("multi_touch_enabled", False))


def _cfg(config: dict | None) -> dict:
    block = (config or {}).get("multi_touch", {})
    return {
        "device":   str(block.get("event_device", "auto")).strip() or "auto",
        "raw_max":  int(block.get("raw_max", 0)),
        "swap_xy":  bool(block.get("swap_xy", False)),
        "invert_x": bool(block.get("invert_x", False)),
        "invert_y": bool(block.get("invert_y", False)),
        "touch_major": max(1, int(block.get("touch_major", 60))),
    }


def _as_root(command: str, mode: str) -> str:
    """``command`` rewritten to run as root under the given mode."""
    template = dict(ROOT_MODES)[mode]
    return template.format(cmd=command, quoted=_quote(command))


def _shell(command: str, timeout: int = 15):
    """Run a shell command as root, however this device grants it."""
    if _root_mode is None:
        raise RuntimeError("no root mode established")
    return _run(["shell", _as_root(command, _root_mode)], timeout=timeout)


def root_mode() -> str | None:
    """Which escalation worked, for the UI to report. None until probed."""
    return _root_mode


def have_root(refresh: bool = False) -> bool:
    """True when shell commands can run as uid 0.

    Each mode is verified by actually running ``id -u`` through it rather
    than by looking for an ``su`` binary — a device can ship an ``su``
    that exists, prompts, and then denies, which would leave every
    gesture silently doing nothing.
    """
    global _root_mode, _probed_root, _last_attempts
    if _probed_root and not refresh:
        return _root_mode is not None
    _probed_root = True
    _root_mode = None
    attempts: list[str] = []
    for mode, _template in ROOT_MODES:
        try:
            done = _run(["shell", _as_root("id -u", mode)], timeout=10)
            out = (done.stdout or b"").decode("utf-8", "replace").strip()
            err = (done.stderr or b"").decode("utf-8", "replace").strip()
        except Exception as exc:
            attempts.append(f"{mode}: {exc}")
            continue
        if out and out.splitlines()[-1].strip() == "0":
            _root_mode = mode
            _last_attempts = f"{mode}: uid 0"
            log.info("Multi-touch: running as root via '%s'.", mode)
            return True
        attempts.append(f"{mode}: {out or err or 'no output'}")
    _last_attempts = "; ".join(attempts)
    log.info(
        "Multi-touch unavailable — nothing returned uid 0. Tried %s.",
        _last_attempts,
    )
    return False


def last_root_attempts() -> str:
    """What each mode answered on the last probe, for the UI."""
    return _last_attempts


def touch_device(cfg: dict, refresh: bool = False) -> tuple[str, int] | None:
    """The touchscreen event node and its ABS_MT_POSITION_X ceiling.

    Auto-detected unless pinned in config, because the node number is not
    portable: it was event9 on the reference phone and is routinely
    something else on an emulator. Picking the wrong one means writing to
    a keyboard or a sensor — accepted without error, and nothing happens.
    """
    global _detected
    if cfg["device"] != "auto":
        return cfg["device"], max(1, cfg["raw_max"] or 4095)
    if _detected is not None and not refresh:
        return _detected
    try:
        # As root: listing input devices reads /dev/input, which the shell
        # user cannot open on a device where this feature is needed at all.
        done = (_shell("getevent -pl", timeout=20) if _root_mode is not None
                else _run(["shell", "getevent -pl"], timeout=20))
        text = (done.stdout or b"").decode("utf-8", "replace")
    except Exception as exc:
        log.error("Multi-touch: could not list input devices: %s", exc)
        return None

    node, best = None, None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("add device"):
            node = stripped.rsplit(" ", 1)[-1]
        elif "ABS_MT_POSITION_X" in stripped and node:
            # "... : value 0, min 0, max 4095, fuzz 0, ..."
            top = 0
            for part in stripped.split(","):
                if "max" in part:
                    try:
                        top = int(part.strip().split()[-1])
                    except ValueError:
                        top = 0
                    break
            if top > 0:
                best = (node, top)
                break                       # first multitouch node wins
    if best is None:
        log.error("Multi-touch: no input device reports ABS_MT_POSITION_X.")
        return None
    _detected = (best[0], cfg["raw_max"] or best[1])
    log.info("Multi-touch: using %s, coordinates 0..%d", *_detected)
    return _detected


def available(config: dict | None = None) -> bool:
    """True when multi-touch is switched on and this device can do it."""
    return enabled() and have_root()


def to_raw(x: int, y: int, cfg: dict, screen: tuple[int, int] | None = None) -> Point:
    """Screen pixel -> driver grid, honouring the configured orientation."""
    width, height = screen or get_active_resolution()
    u = x / float(max(1, width - 1))
    v = y / float(max(1, height - 1))
    if cfg["swap_xy"]:
        u, v = v, u
    if cfg["invert_x"]:
        u = 1.0 - u
    if cfg["invert_y"]:
        v = 1.0 - v
    top = cfg["raw_max"] or 4095
    clamp = lambda t: int(round(min(1.0, max(0.0, t)) * top))   # noqa: E731
    return clamp(u), clamp(v)


def _events(points: Sequence[Point], cfg: dict, screen=None) -> tuple[list[str], list[str]]:
    """The sendevent argument lists for pressing then releasing ``points``."""
    dev = cfg["device"]
    down: list[str] = []
    up: list[str] = []
    for slot, (x, y) in enumerate(points):
        raw_x, raw_y = to_raw(x, y, cfg, screen)
        down += [
            f"sendevent {dev} {_EV_ABS} {_ABS_MT_SLOT} {slot}",
            # A tracking id names one continuous finger; -1 lifts it.
            f"sendevent {dev} {_EV_ABS} {_ABS_MT_TRACKING_ID} {100 + slot}",
            f"sendevent {dev} {_EV_ABS} {_ABS_MT_TOUCH_MAJOR} {cfg['touch_major']}",
            f"sendevent {dev} {_EV_ABS} {_ABS_MT_POSITION_X} {raw_x}",
            f"sendevent {dev} {_EV_ABS} {_ABS_MT_POSITION_Y} {raw_y}",
        ]
        up += [
            f"sendevent {dev} {_EV_ABS} {_ABS_MT_SLOT} {slot}",
            f"sendevent {dev} {_EV_ABS} {_ABS_MT_TRACKING_ID} -1",
        ]
    down.append(f"sendevent {dev} {_EV_KEY} {_BTN_TOUCH} 1")
    down.append(f"sendevent {dev} {_EV_SYN} {_SYN_REPORT} 0")
    up.append(f"sendevent {dev} {_EV_KEY} {_BTN_TOUCH} 0")
    up.append(f"sendevent {dev} {_EV_SYN} {_SYN_REPORT} 0")
    return down, up


def hold_all(
    points: Sequence[Point],
    duration_ms: int,
    config: dict | None = None,
    screen: tuple[int, int] | None = None,
) -> bool:
    """Press every point at once, hold, release. False if it could not run.

    The whole gesture is ONE ``su`` invocation: the press, the sleep and
    the release travel together, so a slow ADB round-trip cannot leave
    fingers stuck down between them. A stuck finger is not a cosmetic
    problem — the game would treat every later gesture as part of one
    endless drag.
    """
    if not points:
        return False
    cfg = _cfg(config)
    if not enabled() or not have_root():
        return False
    resolved = touch_device(cfg)
    if resolved is None:
        return False
    cfg["device"], cfg["raw_max"] = resolved
    if len(points) > MAX_SLOTS:
        log.warning(
            "Multi-touch asked for %d fingers, driver has %d slots — "
            "using the first %d.", len(points), MAX_SLOTS, MAX_SLOTS,
        )
        points = list(points)[:MAX_SLOTS]

    down, up = _events(points, cfg, screen)
    seconds = max(0.0, duration_ms / 1000.0)
    script = "; ".join(down) + f"; sleep {seconds:.2f}; " + "; ".join(up)

    log.info(
        "MULTI-TOUCH %d finger(s) held %dms %s",
        len(points), duration_ms, list(points),
    )
    try:
        done = _shell(script, timeout=int(seconds) + 20)
        err = (done.stderr or b"").decode("utf-8", "replace").strip()
        if done.returncode != 0 or "denied" in err.lower():
            raise RuntimeError(err or f"exit {done.returncode}")
    except Exception as exc:
        # Best effort: lift anything that may still be down, or the next
        # gesture inherits a phantom finger.
        log.error("Multi-touch gesture failed: %s", exc)
        try:
            _shell("; ".join(up), timeout=10)
        except Exception:
            pass
        return False
    time.sleep(0.15)
    return True
