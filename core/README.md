# Core Module (`/core`)

This directory contains the runtime engine, device interface layers, and state tracking structures. It handles process executions and schedules bot events.

---

## File Overview

### 1. `adb_handler.py`
The low-level interface communicating with `2adb.exe` (Android Debug Bridge). It starts subprocess processes, pulls screenshot framebuffers directly to memory via OpenCV, and issues touchscreen inputs.
- **Humanization Features:** 
  - Generates coordinate jitter offsets.
  - Pauses execution with human-like hesitation delays after swipes or taps.
  - Implements coordinate-history memory to prevent double tapping identical screen coordinates.
- **Tap batching (`tap_batch`):** Chains several `input tap` commands into a single `adb shell` invocation, optionally spaced by an on-device `sleep`. An `adb shell` round-trip costs ~38 ms and the on-device `input` binary ~120 ms, so batching removes the round-trip from all but the first tap of a chunk; `input` itself stays the floor. Chunking keeps each call short enough to stay responsive to a stop request.
- **Macros Engine:** Captures shell events from `/dev/input/event*` on the device and parses them into JSON trajectories, which can be replayed to execute custom loops.

### 2. `adb_gestures.py`
Provides high-level multi-finger gesture emulations by invoking multiple parallel coordinates swipes via ADB:
- Pinch-to-zoom out (compresses or expands coordinate spaces).
- Camera panning (dragging across coordinates to pan the camera view in 4 cardinal directions).

### 3. `bot_engine.py`
Runs a background `QThread` execution loop. Controls start, pause, resume, and stop events. 
- Performs health-checks on the Android emulator connection.
- Assures the target game is running in the foreground (attempts auto-launching if it is minimized).
- Evaluates if the bot is locked or frozen on unknown screens and signals user-assistance prompts.
- Passes its village mode into `detect_state()` so the vision layer can skip the template family that cannot appear.
- **Session tally:** `record_attack()` / `record_skip()` count the battles and passed-over villages of the session and emit `stats_changed`. `record_attack_skipped()` moves an already-counted battle back to the skip column — used when V2 turns out to be unable to plan a deploy after the attack was tallied.
- The post-`attack_button2` Confirm grace window (`POST_ATTACK_CONFIRM_WAIT`) is **Ranked only**. In Normal the popup is rare, so the next action-chain iteration handles it instead of spending 4 s on every search.

### 4. `state_machine.py`
Encapsulates state-transitions using a finite state machine logic.
- Verifies screen changes (e.g., `HOME` ➔ `CONFIRMING` ➔ `SEARCHING` ➔ `IN_BATTLE`).
- Rejects invalid state transitions and reports warnings to prevent stuck loops.

### 5. `settings.py`
Singleton managing settings parameters. Automatically loads from and serializes updates to `profiles/settings.json`. Integrates hardware optimization presets (e.g., CPU-only vs Dedicated GPU) to scale search intervals and template limits.

### 6. `logger.py`
Sets up logging directories, formatting models, and redirects warning/info logs to both the standard output and `assets/logs/bot.log`.
