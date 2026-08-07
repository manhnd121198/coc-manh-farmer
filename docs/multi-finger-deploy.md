# Multi-finger deploy

Presses several spots on the base **at the same time** instead of one after
another. Off by default. Turn it on in **Settings → Vision Engine →
"Multi-finger deploy"**.

Only Ring Sweep uses it today.

---

## Why it exists

Ring Sweep picks one drop spot per side of the base and holds each one so
troops pour out. A held card empties at roughly **7 troops per second**.

With one finger the sides are held in turn, so the card drains into
whichever side went first:

| army | hold 5s/side, one finger | four fingers at once |
|---|---|---|
| 50 troops | side 1 gets ~35, side 2 ~15, sides 3-4 **empty** | ~12 per side |
| 200 troops | ~35 per side, ~130 left on the card | ~50 per side |

Four fingers spend the hold window **once**, so every side receives troops
from the first second and the army splits between them.

## Why it needs root

Nothing in the normal ADB toolbox puts two pointers down at once:

- `input tap` / `swipe` / `motionevent` each carry a **single** pointer.
- Two parallel `input swipe` processes are two **independent** one-finger
  drags. Measured on a real phone: the base spanned 868px before two such
  "pinches" and 867px after — the game read a camera pan, not a zoom.
  (Emulators sometimes fuse them; physical devices do not.)

The only route left is writing MT protocol B events straight to the
touchscreen's input node. On a stock phone that is refused — the node is
`u:object_r:input_device:s0` and SELinux is Enforcing — so it takes root.

Emulators (LDPlayer, MEmu, BlueStacks) normally hand out a **root adb
shell** already, so nothing extra is needed there. The code tries the plain
shell first and only falls back to `su`.

## What happens when it can't run

It never blocks an attack. In order:

1. Switch off → one-finger behaviour, no probing at all.
2. Switch on but no uid 0 → logs `Multi-touch unavailable: no way to run
   commands as uid 0`, falls back.
3. No input node reports touch coordinates → logs `no input device reports
   ABS_MT_POSITION_X`, falls back.
4. The gesture itself errors mid-attack → logs `RingSweep: multi-touch hold
   failed — falling back to one side at a time`, lifts any finger that may
   still be down, and holds the sides one by one.

Step 4's cleanup matters: a finger left down turns every later action into
one endless drag, and the bot would appear to stop responding.

---

## Configuration

`config/v2_attack_rules.json` → `"multi_touch"`. Hot-reloadable — press
**Reload Config** in the Smart Vision V2 panel, no restart.

```json
"multi_touch": {
  "event_device": "auto",
  "raw_max": 0,
  "swap_xy": false,
  "invert_x": false,
  "invert_y": false,
  "touch_major": 60
}
```

| key | meaning |
|---|---|
| `event_device` | `"auto"` finds the touchscreen itself. Pin it (`/dev/input/event9`) only if detection picks wrong. |
| `raw_max` | Driver coordinate ceiling. `0` = read it from the detected node. |
| `swap_xy`, `invert_x`, `invert_y` | **The only per-device calibration.** See below. |
| `touch_major` | Reported contact size. 60 works everywhere; not worth touching. |

### Why swap/invert exist

The driver does not speak screen pixels. It reports `ABS_MT_POSITION_X/Y`
on its own grid (`0..raw_max`) covering the **physical panel**, while the
game sees a rotated, resized surface. On the reference phone that was a
1440x3088 portrait panel presented to CoC as 1350x1080 landscape — three
transforms deep.

Deriving that chain analytically is where this goes wrong silently: a wrong
guess taps a real position, just not the one intended, and can hit a
different button. So the mapping is three booleans covering all eight
orientations, settled by looking at where a press actually lands.

---

## Calibrating on a new device (~5 minutes)

Do this **before** switching the feature on. Values are wrong until you do.

**1. Show where touches land**

```
adb shell settings put system pointer_location 1
```

A crosshair and live coordinates now draw over everything.

**2. Confirm the device was detected**

```
adb shell getevent -pl | grep -B6 ABS_MT_POSITION_X
```

Note the `add device N: /dev/input/eventN` line above the match, and the
`max` value on the `ABS_MT_POSITION_X` line. If auto-detection logs a
different node than this one, pin `event_device` by hand.

**3. Aim at a known point and look**

Open CoC so the screen is in its normal landscape orientation. Put a finger
down near the **top-left** of the game area using the values in config, then
read the overlay.

Easiest way is a Python shell in the project directory:

```python
from core import multi_touch
cfg = multi_touch._cfg({"multi_touch": {"event_device": "auto"}})
multi_touch.have_root(refresh=True)          # must print True
multi_touch.hold_all([(200, 200)], 3000, {"multi_touch": {}})
```

Watch the overlay during those 3 seconds:

| where it landed | fix |
|---|---|
| top-left (correct) | done, all three stay `false` |
| top-**right** | `invert_x: true` |
| bottom-**left** | `invert_y: true` |
| bottom-right | `invert_x: true` **and** `invert_y: true` |
| anywhere along the wrong axis (e.g. far left, low) | `swap_xy: true`, then re-test and add inversions |

Re-run after each change until a press at `(200, 200)` lands at the top-left
of the game area.

**4. Verify with two fingers**

```python
multi_touch.hold_all([(200, 200), (1100, 800)], 3000, {"multi_touch": {}})
```

Both crosshairs must appear **together**, at opposite corners. If only one
shows, the driver rejected the second slot — check `raw_max` matches the
node's real ceiling.

**5. Turn the overlay off**

```
adb shell settings put system pointer_location 0
```

Then tick the Settings checkbox.

---

## Troubleshooting

**"Không cách nào chạy được quyền root"** — the probe tries three
escalations and the message lists what each one answered:

| mode | command sent | typical device |
|---|---|---|
| `shell` | `adb shell id -u` | emulator with a root adb daemon |
| `su -c` | `adb shell su -c 'id -u'` | Magisk, classic su |
| `su 0` | `adb shell su 0 sh -c 'id -u'` | toolbox su (takes a uid, no `-c`) |

On LDPlayer: Cài đặt → Mục khác → **Quyền Root** on → **Lưu** → then close
the emulator completely and reopen it. Toggling without a full restart
leaves adbd running with the old permissions, so the probe still sees uid
2000. On a phone, grant the shell root in Magisk when it prompts.

**Nothing happens, no error in the log** — writing to the wrong input node
is accepted silently. Re-run step 2 and pin `event_device`.

**Troops land in the wrong place** — the orientation booleans are wrong for
this device. Redo step 3. Turn the feature off in the meantime; a misplaced
press can hit Surrender.

**Fingers seem stuck / the bot stops responding to taps** — a gesture died
without releasing. The code lifts them on failure, but a hard ADB
disconnect mid-gesture can beat it. Reconnect and run:

```
adb shell input tap 1 1
```

**Works on the phone, not on the emulator (or vice versa)** — expected.
`event_device` and `raw_max` differ, and the orientation booleans usually
differ too, because an emulator is often already landscape while a phone
panel is portrait. Calibrate per device; the config travels with the repo,
so keep a note of which machine the committed values belong to.

> The values currently committed are **uncalibrated defaults** (all three
> booleans `false`). Nobody has verified them on any device yet.

---

## Where the code lives

| file | role |
|---|---|
| `core/multi_touch.py` | root probe, device detection, coordinate mapping, the gesture |
| `logic/rules/ring_sweep_rule.py` | `_hold_sides()` — picks multi-touch or the one-at-a-time fallback |
| `ui/settings_tab.py` | the checkbox (`multi_touch_enabled`) |
| `tests/test_multi_touch.py` | availability, mapping, event-sequence and failure-cleanup tests |

The whole gesture — press, hold, release — is **one** shell invocation on
purpose. If the sleep travelled separately, a slow ADB round-trip would
leave fingers down between calls.
