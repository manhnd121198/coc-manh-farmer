# Config Module (`/config`)

This directory contains the configurations for the Smart V2 CSR (Config-Skills-Rules) attack system. The orchestrator monitors these files and automatically hot-reloads them when modifications are made.

---

## File Overview

### 1. `v2_attack_rules.json`
Specifies global combat limits, image recognition values, and rule configurations:
- **`stand_off_px`:** Safe spacing distance kept from red boundary lines when dropping troops.
- **`deploy_ring`:** Drop points along the red-zone polygon pushed outward, instead of inside an axis-aligned corridor. `enabled` (ON) — falls back to the corridor fan when the ring yields no usable point. `offset_px` — how far outside the polygon the ring sits (defaults to `stand_off_px`). `spacing_px` — distance between candidate points along the contour. `points_per_side` — how many of them are actually used per attack. Keep `offset_px` modest: the ring is meant to hug the base, and the ground beyond the rim is the forest border outside the playable map, where the game rejects every tap.
- **`deploy_edge_bias`:** How far across the safe corridor the fallback fan is pushed — `0.0` = corridor centre, `1.0` = outer rim. **Leave it at `0.0`.** The original reasoning ("the rim cannot hold enemy buildings, so drops there always land") is backwards: the rim is where the *playable map* ends. At `1.0` the fan lands at `screen_width − edge_margin_px − 60` no matter where the base is — measured at x=1800 on a 1920 px screen, in the forest, every tap refused.
- **`deploy_edge_margin_px`:** How far in from the corridor's outer edge that rim sits (default 60). Only meaningful when `deploy_edge_bias` is above zero.
- **`polygon`:** Parameters for base boundary detection (HSV thresholds, morphology kernels).
  - **`hsv_s_min` / `hsv_v_min` / `hsv_red_hue_hi`:** The boundary line was sampled off device screencaps at **H 9–11, S 207–217, V 168–193** — narrow and strongly saturated. Defaults (190 / 140 / 14) sit just below that. The old defaults (150 / 110) plus an orange `10–24` band and eased pink/magenta bands were far looser: on one measured frame the orange band alone contributed 81,661 px against the boundary's 12,149 px, so 87 % of the "boundary" was torches, fires, dirt paths and purple buildings — and the hull tracked whatever happened to be warm that frame.
  - **`include_orange_band` / `include_pink_band`:** Re-enable those extra bands for themes that genuinely render the perimeter orange or pink. Off by default.
  - **`boundary_fragments`:** The line is 2 px thick, so the mask yields a scatter of fragments, never one blob — "largest contour" would pick a decoration. Fragments of a line fill ~1.4 % of their bounding box while a solid decoration fills a third, so `max_fill_ratio` (0.18) keeps the thin ones and `min_bbox_px` (1500) drops specks; the survivors are unioned into one hull. Set `enabled: false` to fall back to the old largest-contour-first order.
- **`isometric`:** Scale values for mapping flat pixels to isometric dimensions.
- **`deploy_pattern`:** Delay offsets between troop drops (measured in milliseconds), plus the hold-to-dump controls:
  - `hold_until_empty` — hold the finger down to deploy continuously instead of tapping a fixed number of times. Per-troop `deploy_mode: "hold" | "tap"` in `v2_troop_profiles.json` overrides it.
  - `hold_chunk_ms` — length of one hold before the deploy bar is re-checked (floor 600 ms; anything shorter reads as a tap).
  - `hold_max_ms` — safety budget. An exhausted card leaves the bar and ends the hold early; the budget only matters if that never happens.
  - `tap_batch_size` — how many taps are chained into one ADB call on the tap path (default 6). An `adb shell` round-trip costs ~38 ms and the on-device `input` binary ~120 ms, so batching removes the round-trip from all but the first tap of a chunk; `input` itself is the floor.
  - `tap_burst_gap_ms` — pause between taps of a burst, executed on the device (default 0). A troop's `stagger_ms` overrides it for that troop.
- **`funnel`:** Target ranges and delays used for clearing secondary structures.
- **`spell_path_fractions`:** Flight distances for spells relative to target lines.
- **`rule_priorities`:** Rules evaluation ordering list.

### 2. `v2_troop_profiles.json`
A directory of settings detailing troop behaviors:
- **`kind`:** Unit type (`ground` vs `air`).
- **`style`:** Drop strategy (e.g. `scout_pairs` to clear traps, `funnel` to clear sides, `fan_wide` for spreads, `cluster` for focused waves).
- **`deployment_spacing_ms`:** Interval time between individual unit placements.

### 3. `v2_spell_profiles.json`
Specifies coordinates logic for spells:
- Maps support spells to deployment parameters (e.g. dropping Rage spells ahead of unit clusters, casting Freeze spells on major defenses).
