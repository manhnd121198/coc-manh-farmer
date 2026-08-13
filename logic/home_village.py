"""
Home Village Logic — V36 Ultimate Death Sensor & Fallback Retreat.

V36 FIXES:
  • Hero Vitality Sensor: Now exactly matches the user's images.
    - Alive (Ability used): Face is gray (low saturation) BUT Green/Yellow/Red Health bar is present.
    - Dead: Face is very dark/black (Brightness < 120, Saturation < 60) AND Health bar is completely absent.
  • Anti-Stuck Retreat: If the bot decides to retreat but can't find the 'End Battle' button, 
    it performs an emergency tap on the exact coordinate (bottom-left) to guarantee escape.
"""

import random
import time
import cv2
import numpy as np

from core.logger import BotLogger
from core.adb_handler import tap, swipe, screencap as adb_screencap
from core.state_machine import StateMachine, GameState
from core.settings import Settings
from vision.screen_reader import ScreenReader
from vision.ocr_reader import OCRReader
from logic import fast_entry
from logic.smart_v2_logic import SmartV2Logic

log = BotLogger.get("home_village")

C_GOLD = "\033[93m"
C_ELIXIR = "\033[95m"
C_DARK = "\033[96m"
C_GREEN = "\033[92m"
C_RED = "\033[91m"
C_RESET = "\033[0m"


class HomeVillageLogic:
    def __init__(self, profile, sm, sr, ocr):
        self._profile = profile
        self._sm: StateMachine = sm
        self._sr: ScreenReader = sr
        self._ocr: OCRReader = ocr
        self._attack_active = False
        self._battle_phase_done = False
        self._engine = None
        
        self._hero_memory: list[tuple[str, int, int]] = []
        self._post_deploy_time: float = 0.0
        self._initial_loot: dict[str, int] = {}

        # How many more villages the current random-skip run still owes.
        # Set when a roll fires, counted down on the villages after it.
        self._random_skips_left: int = 0

        # True when the base we just left was skipped because V2 could not
        # read it. Stops the random skip from piling onto that.
        self._forced_skip_last: bool = False

        # Smart Vision V2 — opt-in per-village. Constructed eagerly so the
        # mode flag can flip mid-session without a restart.
        self._v2 = SmartV2Logic(self._profile, self._sr, self._ocr, mode_key="hv")

    def set_engine(self, engine):
        self._engine = engine
        self._v2.set_engine(engine)

    def update_profile(self, profile: dict):
        self._profile = profile
        self._v2.update_profile(profile)

    def _count_attack(self) -> None:
        """Tally one battle. Every path that arms ``_attack_active`` goes
        through here so Normal, Ranked and loot-skip all count alike."""
        if self._engine is not None:
            self._engine.record_attack()

    def _is_interrupted(self) -> bool:
        if self._engine is None: return False
        return not self._engine._running or self._engine._paused

    def handle(self, screenshot: np.ndarray, state: GameState):
        if state == GameState.HOME:
            self._handle_home(screenshot)
        elif state == GameState.OPPONENT_FOUND:
            self._handle_opponent_found(screenshot)
        elif state == GameState.IN_BATTLE:
            self._handle_in_battle(screenshot)
        elif state == GameState.BATTLE_ENDED:
            self._handle_battle_ended(screenshot)

    def _handle_home(self, screenshot: np.ndarray):
        sequence = self._profile.get("hv_entry_sequence", [])
        if sequence and self._engine:
            self._engine.execute_attack_sequence(sequence)
        elif fast_entry.is_available():
            # Blind-taps Attack -> Find a Match -> Attack!. Skips ~13s of
            # screencap + detect_state; the next engine tick re-reads the
            # screen, so a swallowed tap costs one attempt, not the run.
            fast_entry.run(self._is_interrupted)
        else:
            m = self._sr.find_template_by_name(screenshot, "attack_button")
            if m: tap(m[0], m[1])

    # ── Random skip ─────────────────────────────────────────────────────
    # Taking every village that clears the loot bar is the one habit of this
    # bot that no human has: a person passes on bases they simply don't feel
    # like hitting. When the option is on, a roll every so often skips the
    # next 1-2 villages regardless of loot.
    #
    # Only Normal matchmaking reaches this screen — Ranked drops straight
    # into the battle, so there is nothing to skip there.

    def _random_skip_due(self) -> bool:
        """True if this village should be passed over just to break rhythm."""
        if not self._profile.get("hv_random_skip_enabled", False):
            self._random_skips_left = 0
            return False

        # The village right after one V2 walked away from is taken no matter
        # what the dice say. The two kinds of skip are unrelated — one is
        # deliberate rhythm, the other is the planner failing — and letting
        # them stack turns "skip a base" into "skip three bases", each one
        # paying its own search fee. If V2 gives up on this one too it will
        # skip again on its own; that chain is fine, it just must not be
        # lengthened by the dice.
        if self._forced_skip_last:
            self._forced_skip_last = False
            log.debug("Random skip suppressed — the previous base was already skipped.")
            return False

        # Already inside a run that the previous roll started.
        if self._random_skips_left > 0:
            self._random_skips_left -= 1
            return True

        chance = int(self._profile.get("hv_random_skip_chance", 20))
        if chance <= 0 or random.randint(1, 100) > chance:
            return False

        low = max(1, int(self._profile.get("hv_random_skip_min", 1)))
        high = max(low, int(self._profile.get("hv_random_skip_max", 2)))
        # This village is the first of the run, so the rest is what's left.
        self._random_skips_left = random.randint(low, high) - 1
        return True

    def _tap_next(self, screenshot: np.ndarray) -> None:
        m = self._sr.find_template_by_name(screenshot, "next_button")
        if m:
            tap(m[0], m[1])

    def _handle_opponent_found(self, screenshot: np.ndarray):
        _s = Settings()

        # Checked before the loot OCR — a village we are going to pass on
        # anyway is not worth a read.
        if self._random_skip_due():
            log.info(
                "%s⤳ Random skip%s — passing on this village (%d more to go).",
                C_ELIXIR, C_RESET, self._random_skips_left,
            )
            if self._engine is not None:
                self._engine.record_skip()
            self._tap_next(screenshot)
            return

        # Skip loot check if user disabled it
        if _s.get("skip_loot_ocr", False):
            log.info("%s✓ Loot Skip ON%s — attacking without reading!", C_GREEN, C_RESET)
            self._count_attack()
            self._attack_active = True
            self._battle_phase_done = False
            self._initial_loot = {}
            self._execute_full_attack(screenshot)
            return

        loot = self._ocr.read_loot(screenshot)
        gold, elixir, dark = loot.get("gold", 0), loot.get("elixir", 0), loot.get("dark_elixir", 0)
        mg, me = self._profile.get("min_gold", 200000), self._profile.get("min_elixir", 200000)

        if gold >= mg or elixir >= me:
            log.info("%s✓ Loot OK%s (G:%d E:%d) — ATTACKING!", C_GREEN, C_RESET, gold, elixir)
            self._count_attack()
            self._attack_active = True
            self._battle_phase_done = False
            self._initial_loot = loot
            self._execute_full_attack(screenshot)
        else:
            log.info("%s✗ Skip%s (G:%d E:%d)", C_RED, C_RESET, gold, elixir)
            if self._engine is not None:
                self._engine.record_skip()
            self._tap_next(screenshot)

    # ── Ranked-only helpers ─────────────────────────────────────────────
    # Ranked matchmaking skips the OPPONENT_FOUND scout screen entirely:
    # the moment matchmaking succeeds, the player is dropped straight
    # into IN_BATTLE. That means our normal flow (HOME → CONFIRMING →
    # OPPONENT_FOUND → activate attack) never fires `_attack_active`.
    # The two helpers below auto-activate the attack on first IN_BATTLE
    # entry, but ONLY when the user picked Ranked. Normal mode and BB
    # are completely unaffected.
    def _is_ranked_mode(self) -> bool:
        return str(self._profile.get("hv_match_mode", "normal")).lower() == "ranked"

    def _ranked_auto_activate(self, screenshot: np.ndarray) -> None:
        """Ranked-only kick-off. Reads the loot bar (so retreat thresholds
        still work) and arms ``_attack_active`` so the next tick deploys."""
        log.info("%sRANKED battle started — auto-activating attack.%s", C_GREEN, C_RESET)
        if Settings().get("skip_loot_ocr", False):
            self._initial_loot = {}
        else:
            try:
                self._initial_loot = self._ocr.read_loot(screenshot)
                g = self._initial_loot.get("gold", 0)
                e = self._initial_loot.get("elixir", 0)
                d = self._initial_loot.get("dark_elixir", 0)
                log.info("Ranked initial loot — G:%d E:%d DE:%d", g, e, d)
            except Exception as exc:
                log.warning("Ranked loot read failed: %s — continuing without it.", exc)
                self._initial_loot = {}
        self._count_attack()
        self._attack_active = True
        self._battle_phase_done = False

    def _handle_in_battle(self, screenshot: np.ndarray):
        # ── RANKED-ONLY auto-activation ────────────────────────────────
        # If the attack isn't active yet but we are already IN_BATTLE,
        # this can only happen in Ranked (Normal always passes through
        # OPPONENT_FOUND first). Kick the attack off here.
        if not self._attack_active and not self._battle_phase_done and self._is_ranked_mode():
            self._ranked_auto_activate(screenshot)

        if self._attack_active and not self._battle_phase_done:
            self._execute_full_attack(screenshot)

        # ── V36: Active Monitoring Phase ────────────────
        if self._attack_active and self._battle_phase_done:
            # NEW: post-deployment countdown → silent end-of-battle
            if self._check_deploy_timer(screenshot):
                return

            if self._profile.get("auto_retreat_enabled", False) or self._profile.get("retreat_time", 0) > 0:
                self._check_auto_retreat(screenshot)
            
            if self._profile.get("retreat_heroes_dead", False):
                self._check_hero_death_retreat(screenshot)

    def _abandon_base(self, screenshot: np.ndarray):
        """V2 refused to deploy and the user asked for a new opponent.

        The way out depends on how far in we are, and the two cost very
        different amounts. On the scouting screen "Next" walks away for the
        price of the search. Once the battle has started the only exit is
        surrendering, which spends the attack and the trophies with it —
        that is the price of not letting the legacy planner dump the army
        into a base V2 could not read.
        """
        self._attack_active = False
        self._battle_phase_done = False
        # Take the next base regardless of the dice, and drop any random-skip
        # run that was still owed — this skip already cost a search.
        self._forced_skip_last = True
        self._random_skips_left = 0
        if self._engine is not None:
            self._engine.record_attack_cancelled()

        if self._sm.state == GameState.OPPONENT_FOUND:
            log.info("%sV2 gave up — taking Next to a new base.%s", C_ELIXIR, C_RESET)
            self._tap_next(screenshot)
        else:
            log.info(
                "%sV2 gave up mid-battle — surrendering to find a new base.%s",
                C_ELIXIR, C_RESET,
            )
            self._end_battle(screenshot)

    def _handle_battle_ended(self, screenshot: np.ndarray):
        self._attack_active = False
        self._battle_phase_done = False
        self._hero_memory.clear()
        m = self._sr.find_template_by_name(screenshot, "return_home")
        if m: tap(m[0], m[1])

    # ═══════════════════════════════════════════════════════════════════
    #  V36 VITALITY SENSOR & TACTICAL RETREAT
    # ═══════════════════════════════════════════════════════════════════

    def _is_hero_dead(self, screenshot: np.ndarray, hx: int, hy: int) -> bool:
        """
        V36 Perfect Sensor:
        Checks the exact memory coordinate of the hero card.
        1. Is there a health bar? (Checks upper area for Bright Green/Yellow/Red).
        2. Is the face dark? (Brightness < 140, Saturation < 60).
        If (No Health Bar) AND (Face is Dark/Gray) -> Returns True (DEAD).
        """
        h, w = screenshot.shape[:2]
        
        # 1. Health Bar Check (Directly above the center of the icon)
        bar_y1, bar_y2 = max(0, hy - 80), max(0, hy - 30)
        bar_x1, bar_x2 = max(0, hx - 40), min(w, hx + 40)
        bar_roi = screenshot[bar_y1:bar_y2, bar_x1:bar_x2]
        
        health_found = False
        if bar_roi.size > 0:
            hsv_bar = cv2.cvtColor(bar_roi, cv2.COLOR_BGR2HSV)
            # Detect CoC specific bright health bar colors
            mask_g = cv2.inRange(hsv_bar, np.array([35, 150, 150]), np.array([85, 255, 255]))
            mask_y = cv2.inRange(hsv_bar, np.array([10, 150, 150]), np.array([35, 255, 255]))
            mask_r1 = cv2.inRange(hsv_bar, np.array([0, 150, 150]), np.array([10, 255, 255]))
            mask_r2 = cv2.inRange(hsv_bar, np.array([170, 150, 150]), np.array([180, 255, 255]))
            
            health_px = np.count_nonzero(mask_g | mask_y | mask_r1 | mask_r2)
            if health_px > 5:
                health_found = True

        # 2. Face Color Check (Center of the icon)
        face_roi = screenshot[max(0, hy-20):min(h, hy+20), max(0, hx-20):min(w, hx+20)]
        is_gray_and_dark = False
        if face_roi.size > 0:
            hsv_face = cv2.cvtColor(face_roi, cv2.COLOR_BGR2HSV)
            s_mean = np.mean(hsv_face[:, :, 1]) # Saturation
            v_mean = np.mean(hsv_face[:, :, 2]) # Brightness
            
            # Ability Used (Gray but bright) vs Dead (Gray and Darkened)
            if s_mean < 60 and v_mean < 140:
                is_gray_and_dark = True
                
        # The ultimate death check
        if not health_found and is_gray_and_dark:
            return True
            
        return False

    def _check_hero_death_retreat(self, screenshot: np.ndarray):
        # Wait 20 seconds AFTER the entire deployment finishes before checking
        if time.time() - self._post_deploy_time < 20.0:
            return
            
        if not self._hero_memory: return
            
        warden_dead = False
        other_dead = 0
        
        for name, hx, hy in self._hero_memory:
            if self._is_hero_dead(screenshot, hx, hy):
                if name == "grand_warden": warden_dead = True
                else: other_dead += 1
                    
        if warden_dead and other_dead >= 1:
            log.info("%sTACTICAL RETREAT: Grand Warden and %d other hero(es) are dead!%s", C_RED, other_dead, C_RESET)
            self._end_battle(screenshot)
            self._attack_active = False

    def _check_auto_retreat(self, screenshot: np.ndarray):
        loot = self._ocr.read_loot(screenshot)
        current_g = loot.get("gold", 0)
        current_e = loot.get("elixir", 0)
        
        stolen_g = max(0, self._initial_loot.get("gold", 0) - current_g)
        stolen_e = max(0, self._initial_loot.get("elixir", 0) - current_e)

        if self._profile.get("auto_retreat_enabled", False):
            rg = self._profile.get("retreat_gold", 50000)
            re_ = self._profile.get("retreat_elixir", 50000)
            rd = self._profile.get("retreat_dark_elixir", 500)

            if current_g <= rg and current_e <= re_ and loot.get("dark_elixir", 0) <= rd:
                log.info("%sAUTO-RETREAT: Loot Remaining Low (Stolen: G:%d, E:%d)%s", C_RED, stolen_g, stolen_e, C_RESET)
                self._end_battle(screenshot)
                self._attack_active = False
                return

        rt = self._profile.get("retreat_time", 0)
        if rt > 0 and not Settings().get("skip_timer_ocr", False):
            timer_secs, _ = self._ocr.read_timer_v2(screenshot)
            if 0 < timer_secs <= rt:
                log.info("%sAUTO-RETREAT: Timer reached %ds! (Stolen: G:%d, E:%d)%s", C_RED, timer_secs, stolen_g, stolen_e, C_RESET)
                self._end_battle(screenshot)
                self._attack_active = False

    def _check_deploy_timer(self, screenshot: np.ndarray) -> bool:
        """
        Silent post-deployment countdown.
        Once the player's first wave finishes deploying, we count from
        ``self._post_deploy_time`` and end the battle automatically after
        ``deploy_timer_seconds`` if ``deploy_timer_enabled`` is on.

        Returns True if a retreat was triggered (caller should stop further checks).
        """
        if not self._profile.get("deploy_timer_enabled", False):
            return False
        if self._post_deploy_time <= 0:
            return False
        seconds = int(self._profile.get("deploy_timer_seconds", 90))
        if seconds <= 0:
            return False
        elapsed = time.time() - self._post_deploy_time
        if elapsed >= seconds:
            log.info(
                "%sDEPLOY TIMER: %.0fs elapsed since deployment → ending battle.%s",
                C_RED, elapsed, C_RESET,
            )
            self._end_battle(screenshot)
            self._attack_active = False
            self._post_deploy_time = 0.0
            return True
        return False

    def _end_battle(self, screenshot: np.ndarray):
        log.info("Ending battle early...")
        h, w = screenshot.shape[:2]

        # ── Step 1: locate the surrender / end-battle button ──────────────
        # Same on-screen button — the label flips between
        # "End Battle", "Surrender", "Exit" or "Yield" depending on the
        # game state and account language. We try, in order:
        #   1) end_battle_button template
        #   2) surrender_button   template  (same position, different label)
        #   3) OCR text search ("end battle", "surrender", "exit", "yield")
        #   4) fixed-position emergency tap (last resort).
        target = self._sr.find_template_by_name(screenshot, "end_battle_button")
        source = "end_battle_button"
        if target is None:
            target = self._sr.find_template_by_name(screenshot, "surrender_button")
            if target is not None:
                source = "surrender_button"
        if target is None:
            ocr_hit = self._ocr.find_text_in_region(
                screenshot,
                keywords=["end battle", "surrender", "exit", "yield"],
                region=(0.70, 1.00, 0.00, 0.30),
            )
            if ocr_hit:
                target = ocr_hit
                source = "OCR"

        if target is not None:
            log.info("Surrender button via %s at %s.", source, target)
            tap(target[0], target[1])
        else:
            # All three layers failed — emergency tap on the bottom-left.
            log.warning("Surrender button not detected by template OR OCR — emergency tap!")
            tap(int(w * 0.08), int(h * 0.85))

        time.sleep(1.0)

        # ── Step 2: dismiss the confirmation popup ────────────────────────
        ss = adb_screencap()
        if ss is None:
            return
        cm = self._sr.find_template_by_name(ss, "end_battle_confirm")
        if cm:
            tap(cm[0], cm[1])
            return
        # OCR fallback for the popup ("OK" / "Confirm" / "Yes" / "End Battle").
        ocr_hit = self._ocr.find_text_in_region(
            ss,
            keywords=["ok", "confirm", "yes", "end battle", "surrender"],
            region=(0.40, 0.85, 0.30, 0.85),
        )
        if ocr_hit:
            tap(ocr_hit[0], ocr_hit[1])
        else:
            tap(int(w * 0.60), int(h * 0.65))

    # ═══════════════════════════════════════════════════════════════════
    #  V36 ORDERED DEPLOYMENT
    # ═══════════════════════════════════════════════════════════════════

    def _execute_full_attack(self, screenshot: np.ndarray):
        # ── Smart Vision V2 fast path ──────────────────────────────────
        if self._v2.is_enabled():
            log.info("═══ SMART VISION V2 — HV ═══")
            if not self._v2.execute(screenshot):
                self._abandon_base(screenshot)
            return

        h, w = screenshot.shape[:2]
        ui_cutoff = self._sr.get_ui_cutoff(h)

        log.info("═══ V36 ORDERED DEPLOY & SMART RETREAT ═══")

        line, _ = ScreenReader.get_focused_deployment_line(screenshot, ui_cutoff, 15)
        if not line: line = [(w // 4, h // 4)]

        cluster_pt = line[len(line) // 2]
        cluster_x, cluster_y = cluster_pt[0], cluster_pt[1]
        true_core_x, true_core_y = w // 2, ui_cutoff // 2

        fresh_ss = adb_screencap()
        if fresh_ss is None: fresh_ss = screenshot

        # ── STEP 1: TROOPS (Ordered from Drag & Drop UI) ──────────────
        selected_troops = self._profile.get("selected_troops", [])
        for troop_name in selected_troops:
            if self._is_interrupted(): return
            troop_loc = self._sr.find_template_by_name(fresh_ss, troop_name)
            if troop_loc is None: continue

            tap(troop_loc[0], troop_loc[1])
            time.sleep(0.2)
            log.info("Dumping ALL %s...", troop_name)
            _s = Settings()
            _sd = _s.get("swipe_duration", 2500)
            swipe(cluster_x, cluster_y, cluster_x + 10, cluster_y + 10, duration_ms=_sd)
            time.sleep(0.2)
            swipe(cluster_x, cluster_y, cluster_x - 10, cluster_y - 10, duration_ms=int(_sd * 0.8))

        # ── STEP 2: HEROES (Ordered from UI) ───────────────
        selected_heroes = self._profile.get("selected_heroes", [])
        self._hero_memory.clear()
        
        for hero_name in selected_heroes:
            if self._is_interrupted(): return
            hero_loc = self._sr.find_template_by_name(fresh_ss, hero_name)
            if hero_loc is None: continue

            self._hero_memory.append((hero_name, hero_loc[0], hero_loc[1]))
            tap(hero_loc[0], hero_loc[1])
            time.sleep(0.2)
            tap(cluster_x + random.randint(-15, 15), cluster_y + random.randint(-15, 15))
            time.sleep(0.3)

        # ── STEP 3: HERO ABILITY WAIT ────────────────────────────────
        # Roll trong dải đã cấu hình, nên không trận nào kích kỹ năng
        # đúng cùng một nhịp. Đọc qua object V2 để đường cũ này và luật
        # V2 dùng chung một cấu hình.
        _ability_delay = self._v2.hero_ability_delay()
        log.info("Waiting %.1f seconds for the army to engage...", _ability_delay)
        _steps = max(1, int(_ability_delay / 0.5))
        for _ in range(_steps):
            if self._is_interrupted(): return
            time.sleep(0.5)

        # ── STEP 4: HERO ABILITIES (Memory Slot Double-Tap) ──────────────
        if not self._v2.hero_ability_enabled():
            if self._hero_memory:
                log.info(
                    "Hero abilities on auto — leaving the %d hero(es) to fire "
                    "their own at low health.", len(self._hero_memory),
                )
        else:
            for hero_name, hx, hy in self._hero_memory:
                if self._is_interrupted(): return
                tap(hx, hy)
                time.sleep(0.1)
                tap(hx, hy)
                time.sleep(0.3)

        # ── STEP 5: SMART SPELL DISTRIBUTION (Ordered from UI) ───────
        spell_ss = adb_screencap()
        if spell_ss is None: spell_ss = fresh_ss
            
        selected_spells = self._profile.get("selected_spells", [])
        spread_depths = [0.55, 0.75, 0.95, 1.1, 0.85, 0.95, 1.0]
        #spread_depths = [0.45, 0.65, 0.85, 1.0, 0.55, 0.75, 0.95]

        for spell_name in selected_spells:
            if self._is_interrupted(): return
            spell_loc = self._sr.find_template_by_name(spell_ss, spell_name)
            if spell_loc is None: continue

            tap(spell_loc[0], spell_loc[1])
            time.sleep(0.2)
            
            for depth in spread_depths:
                if self._is_interrupted(): return
                spell_x = int(cluster_x + (true_core_x - cluster_x) * depth)
                spell_y = int(cluster_y + (true_core_y - cluster_y) * depth)
                spell_x = max(10, min(spell_x, w - 10))
                spell_y = max(100, min(spell_y, ui_cutoff - 20))

                tap(spell_x + random.randint(-20, 20), spell_y + random.randint(-20, 20))
                time.sleep(0.15)

        log.info("═══ V36 ATTACK COMPLETE ═══")
        self._battle_phase_done = True
        
        # Start Grace Period Timer for Hero Retreat
        self._post_deploy_time = time.time()