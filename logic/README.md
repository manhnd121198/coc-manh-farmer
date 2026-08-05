# Logic Module (`/logic`)

This directory coordinates target selections, troop/spell drops, hero skill triggers, and state routines.

---

## File Overview

### 1. `home_village.py`
Manages Main Village gameplay:
- Handles standard search phases: reads loot via OCR; clicks "Next" if resources are too low, and initiates attacks once criteria are met.
- Performs attack deployment loops, tracks hero usage, and executes emergency taps if surrender button templates fail.
- Auto-handles the difference between standard and Ranked matchmaking states.
- `_skip_unplannable_base()`: when V2 cannot plan a deploy, taps `next_button`, clears the attack state, and re-tallies the battle as a skip (`BotEngine.record_attack_skipped()`).
- **Post-deploy countdown**: `_resolve_deploy_deadline()` draws the surrender delay once per battle from `deploy_timer_seconds` … `deploy_timer_seconds_max` (default 110–120 s) and keeps it for that deployment. Drawing per tick would collapse the range to its minimum. Leave `deploy_timer_seconds_max` out (or equal to the minimum) for a fixed timer, as before.

### 2. `builder_base.py`
Manages Builder Village combat stages:
- Coordinates search flows and launches matchmaking.
- Supports Stage 1 and Stage 2 transitions.
- **Fresh Screenshot Re-scan:** Re-screens the battlefield immediately after dropping heroes to update template indexes, bypass HP bar overlays, and deploy remaining troops accurately.
- Periodically activates Hero skills based on user profile timing configurations.

### 3. `smart_v2_logic.py`
Wrapper coordinating V2 attack steps:
- Dispatches execution tasks to the `V2Orchestrator`.
- Acts as a fallback proxy: if the orchestrator fails, it drops down to the legacy V36 single-cluster deployment method.
- `execute(screenshot, allow_legacy=True) -> bool` reports whether an attack happened. With `allow_legacy=False` it returns `False` instead of running V36, so the caller can decide — Home Village uses this to press **Next** and skip a village the orchestrator could not plan (setting `v2_skip_on_fallback`, ON by default). `run_legacy()` remains available for callers that must deploy anyway (Ranked has no Next button mid-battle).

### 4. `v2_orchestrator.py`
The dispatcher for the Config-Skills-Rules (CSR) attack system:
- Monitors file modification timestamps (`mtime`) on configuration files (`config/*.json`) to auto-reload parameters.
- Allocates vision/logic skills.
- Resolves the best strategy rule (Air, Ground Funnel, Snipe) matching the current troop profile.
- Adjusts zoom ratios prior to starting vision checks.

---

## Subfolders

### Rules (`/logic/rules`)
Includes specialized strategy executors inheriting from `BaseRule`:
- **`air_attack_rule.py`:** Drops air units along safe vectors.
- **`ground_funnel_rule.py`:** Deploys units in a two-sided pattern to clear secondary defenses before launching main waves.
- **`th_snipe_rule.py`:** Pins down coordinates closest to the target Town Hall.
- **`resource_raid_rule.py`:** Drops scouts on individual storage points.
- **`perimeter_sweep_rule.py`:** Swipes a closed loop around the four safe corridors, with a random start point and direction per troop; falls back to `smart_default` when fewer than four sides are detected. Parameters live under `perimeter_sweep` in `config/v2_attack_rules.json`.
- **`smart_default_rule.py`:** Uses the widest safe path corridor to deploy — also the in-orchestrator fallback when the selected rule declines.

**Tap bursts** (`human_touch.tap_burst` → `adb_handler.tap_batch`): the deploy taps are planned first (red-zone check included), then fired in chunks of one ADB call each, without the per-tap randomized pause. Measured ~275 ms → ~127 ms per tap. A troop's `stagger_ms` still applies but runs as an on-device `sleep` instead of a Python one.

**Hold-to-dump** (`base_rule._hold_dump`, off by default): CoC deploys continuously while a finger stays down, so the army dump holds instead of tapping. The stop condition is vision-based — an exhausted card is removed from the deploy bar, so `find_one()` returning `None` means "all deployed". Because cards vanish and the bar shifts, every rule now re-screencaps before locating the *next* troop card. Controlled by `deploy_pattern.hold_until_empty` (see `config/README.md`); the tap paths remain and are used when it is off.

### Skills (`/logic/skills`)
Coordinates mechanical troop/spell deployments:
- **`funnel_planner.py`:** Calculates funnel drop targets.
- **`ring_planner.py`:** Deploy points along the red-zone polygon **offset outward**, walked at a fixed spacing and grouped by the side they face. This replaces the corridor fan as the default for `air_attack` (`deploy_ring.enabled`, ON), because the corridor model could not express the ground that is actually usable: corridors are axis-aligned rectangles built from the polygon's *bounding box*, but a base is a diamond, so its rim runs diagonally and therefore lies *inside* that box. Measured on one real frame the hull covered 678k px against a 892k px bbox — 24 % of the box, the entire diagonal rim, was unreachable, and three of the four corridors came out with negative width because the bbox already touched the screen edge. The ring also never runs out to the screen edge, which after a zoom-out is the forest border outside the playable map. Pure arithmetic, no cv2, so the geometry is unit-tested directly (`tests/test_ring_planner.py`).
- **`fan_planner.py`:** Distributes troops evenly along a corridor. Still the fallback path when the ring yields nothing. Orientation comes from the corridor's **side name**, not its aspect ratio — a left/right corridor is often wider than tall (579x546 on 16:9) and reading that as "horizontal" runs the fan from the base outwards, planting the first drops on the decorations hugging the base. `edge_bias` (config `deploy_edge_bias`) then slides the whole fan across to the outer rim.
- **`hero_planner.py`:** Manages hero placements and ability timings.
- **`spell_planner.py`:** Positions support spells along army push lines.
- **`human_touch.py`:** Adds delays and coordinate jitter offsets.
