# Vision Module (`/vision`)

This directory processes screenshot frames, extracts values via text recognition, and performs coordinate segmentations.

---

## File Overview

### 1. `screen_reader.py`
The primary image processing engine. Takes screenshots and scans for templates:
- **Fast paths (all fail-safe — a miss only costs the old, slower work):**
  - `detect_state(screenshot, mode)` — passing `"home_village"` skips the seven Builder Base templates that cannot match there (~17% of a full classification). Omit `mode` and everything is checked, as before.
  - `_ui_scale_memo` — remembers which scale a UI template matched at for the current screen height and tries that one first, turning four full-frame matches into one (~3.7× on a hit). A miss falls back to the full sweep and drops the memo.
  - `_conf_cache_sig` / `_conf_cache_val` — `scan_for_confirmations()` caches its result per frame (shape + sampled hash), so the action chain no longer re-matches the six confirmation templates that `detect_state()` just matched.
  - `clear_cache()` clears all three alongside the template cache.
- **UI Elements:** Employs standard template matching with high thresholds (e.g. `0.80`) to identify static menu assets.
- **Troop/Spell/Hero Cards:** Scrapes the lower HUD strip (below the battlefield cutoff), converting screenshots to grayscale, resizing assets using multiple search scales (from `0.7x` to `1.1x` by default), and matching elements.
- **Battlefield Bounding Box:** Detects red boundary lines using HSV threshold ranges to find safe grid coordinates.

### `skills/red_zone_polygon.py`
Turns the perimeter into a closed polygon. Two properties of the target drive the design and are easy to get wrong:
- The line is **narrow in colour** — sampled at H 9–11, S 207–217, V 168–193. Anything looser pulls in torches, fires, dirt paths and purple buildings; measured on one frame, those made up 87 % of the mask and the hull then tracked them instead of the boundary (too wide on one base, 22 % of the screen on the next). See `config/README.md` for the bands.
- The line is **thin**, so the mask is a scatter of fragments rather than one blob. Attempt 0 therefore keeps contours that fill under `max_fill_ratio` of their bounding box — a line fills ~1.4 %, a decoration a third — and unions them. Only if that fails does it fall back to largest-contour and then top-K fusion.

`polygon.debug_dump` writes `redzone_<mode>_<ts>.png` on sanity failure; `AttackRule._dump_plan` writes `plan_<label>_<ts>.png` plus an un-annotated `_raw.png` for every accepted plan. Tune against the raw frame — the overlay strokes the polygon in exactly the red the mask hunts for.
- **Deployment Line Generator:** Calculates coordinate lists around the detected battlefield grid margins to drop troops.

### 2. `ocr_reader.py`
Integrates **EasyOCR** for text readings.
- **Loot Reading:** Isolates the top-left area, dividing it into three horizontal bars (Gold, Elixir, Dark Elixir). Pre-processes these regions using CUBIC interpolation and Otsu's thresholding, runs the character recognition reader, and filters characters to extract clean digits.
- **Timer Reading:** Identifies battle time strings (e.g. `2m 45s`, `02:45`, `45s`) to track battle progress.
- **Button Finder:** Uses keyword list searches within region boundaries to locate dynamic text buttons (like dynamic translations of "End Battle", "Surrender", "Exit").

### 3. `smart_vision_v2.py`
Advanced computer vision segmenter. It processes HSV masks of the red deployment lines, projects contours onto an isometric coordinate grid, and determines the closest deployment node to target structures.

### 4. `template_manager.py`
Helper module checking file paths. Reads `assets/templates/manifest.json` and loads image template arrays into a caching map to prevent redundant read operations from disk.

---

## Skills Subfolder (`/vision/skills`)
Contains modular computer vision functions:
- **`red_zone_polygon.py`:** Generates coordinates describing the boundaries of red zones.
- **`isometric_grid.py`:** Standard grid projection maps translating flat 2D pixels to angled 3D isometric tiles.
- **`target_locator.py`:** Scans for structural templates (like Town Halls or resource storages).
- **`obstacle_detector.py`:** Identifies obstacles (trees, rocks, etc.) that block screen views.
- **`safe_corridor.py`:** Isolates paths between the outer boundary and target nodes.
