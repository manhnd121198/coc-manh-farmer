"""
RedZonePolygonSkill — detect the deploy-boundary as a CLOSED POLYGON
(not a bounding box).

Why a polygon: real bases have non-rectangular outlines (crosses,
T-shapes, etc). A bbox treats those bases as solid rectangles and
produces "safe" cluster points that are actually inside the no-deploy
zone in 30%+ of cases.

Pipeline (HSV pass):
    1. UI-band masking — zero out the top loot/timer strip and the
       bottom troop bar BEFORE colour matching, so the loot icons,
       timer banner, and deck don't pollute the red mask.
    2. HSV mask of red/orange/pink dashes with STRICT saturation so the
       semi-transparent UI overlays (timer banner, ranking emblems)
       don't qualify.
    3. Two-axis morphological close to bridge dash gaps.
    4. Largest external contour, convex hull, Douglas-Peucker.
    5. Sanity validation — reject polygons that touch the top edge
       (UI contamination), cover almost the entire playfield, or have
       degenerate aspect ratio.
    6. Multi-contour fusion fallback — when the LARGEST single contour
       fails sanity (typically because the morph close didn't bridge
       top↔bottom dashes and we caught only a horizontal slice), the
       skill stacks the top-K contours and tries again on the combined
       convex hull. This recovers fragmented detections without
       loosening the per-contour gates.

Optional YOLO completeness guard: a small CoC-specific segmentation model
finds the BaseArea before the colour passes. It does NOT replace the red-line
polygon. It rejects an HSV/inversion candidate when that polygon clips a
significant part of the detected base, then lets contour fusion/fallback try.

Fallback (inversion pass): if the HSV pass returns nothing valid, the
detector inverts the image and re-runs the same pipeline against
CYAN (which is what the perimeter dashes turn into after a bitwise NOT).
This is the user-suggested "color inversion" trick: it isolates the
perimeter line clearly when the base theme is dark/blue/snow and the
standard HSV pass struggles.

The inverted screenshot is ONLY used to compute the polygon. After
that the orchestrator goes back to the original full-colour screenshot
for every other vision step (troop card lookup, building location,
obstacle detection, etc.).

Debug dumps: set ``polygon.debug_dump`` to a directory path in
``v2_attack_rules.json`` and the skill will write
``redzone_<mode>_<ts>.png`` overlays whenever a pass fails sanity, so
you can SEE exactly what the detector caught.

Returns Nx2 int32 vertex array, or None when no valid polygon detected.
"""

from __future__ import annotations

import os
import time
from typing import List, Optional

import cv2
import numpy as np

from core.logger import BotLogger

log = BotLogger.get("v2.red_zone_polygon")


class RedZonePolygonSkill:
    name = "red_zone_polygon"
    _yolo_model = None
    _yolo_model_path = ""
    _yolo_failed_paths: set[str] = set()

    def detect(
        self,
        screenshot: np.ndarray,
        ui_cutoff: int,
        config: dict | None = None,
    ) -> Optional[np.ndarray]:
        """Return Nx2 int32 polygon vertices, or None if not detected."""
        if screenshot is None:
            return None
        cfg = (config or {}).get("polygon", {}) if config else {}
        h, w = screenshot.shape[:2]
        ui_cutoff = max(1, min(ui_cutoff, h))

        guard = None
        if bool(cfg.get("yolo_guard_enabled", False)):
            guard = self._detect_yolo_guard(screenshot, ui_cutoff, cfg)
        # Keep the YOLO base hull for this frame so a rule (Ring Sweep) can
        # build a deploy corridor straight from it without a second inference.
        self._last_yolo_base = guard

        verts = self._run_pass(
            screenshot, ui_cutoff, cfg, mode="hsv", guard=guard,
        )
        if verts is not None:
            return verts

        if bool(cfg.get("use_inversion_fallback", True)):
            log.info("RedZone HSV pass failed sanity — retrying with colour-inversion fallback.")
            verts = self._run_pass(
                screenshot, ui_cutoff, cfg, mode="inversion", guard=guard,
            )
            if verts is not None:
                return verts

        log.warning("RedZone polygon: detection FAILED on both HSV and inversion passes.")
        return None

    def yolo_base_polygon(self) -> Optional[np.ndarray]:
        """The YOLO base hull cached by the last ``detect`` call, or None.

        Ring Sweep reuses this so enabling the YOLO corridor costs no extra
        inference: ``detect`` already ran the model as its completeness guard
        on the very same screenshot.
        """
        return getattr(self, "_last_yolo_base", None)

    def detect_yolo_base(
        self,
        screenshot: np.ndarray,
        ui_cutoff: int,
        config: dict | None = None,
    ) -> Optional[np.ndarray]:
        """Run the YOLO segmentation on demand and return the base hull.

        Used when the guard was disabled (so ``detect`` never cached one)
        but a rule still wants the YOLO base to build a deploy corridor.
        """
        if screenshot is None:
            return None
        cfg = (config or {}).get("polygon", {}) if config else {}
        h = screenshot.shape[0]
        ui_cutoff = max(1, min(ui_cutoff, h))
        base = self._detect_yolo_guard(screenshot, ui_cutoff, cfg)
        self._last_yolo_base = base
        return base

    # ── Internals ─────────────────────────────────────────────────
    def _run_pass(
        self,
        screenshot: np.ndarray,
        ui_cutoff: int,
        cfg: dict,
        mode: str,
        guard: np.ndarray | None = None,
    ) -> Optional[np.ndarray]:
        h, w = screenshot.shape[:2]
        roi = screenshot[:ui_cutoff, :]
        if mode == "inversion":
            roi = cv2.bitwise_not(roi)
            mask = self._build_mask_inverted(roi, cfg)
        else:
            mask = self._build_mask(roi, cfg)

        # Zero out the top loot/timer strip and the side loot panels so
        # they cannot produce a polygon vertex on the screen border.
        top_excl = int(cfg.get("top_ui_exclude_px", 150))
        if top_excl > 0:
            mask[:min(top_excl, mask.shape[0]), :] = 0
        # Side strips: 'Available Loot' panel on the left and the ranked
        # gold/elixir bars on the right. Width comes from config so each
        # device can tune it without re-shipping code.
        side_excl_l = int(cfg.get("left_ui_exclude_px", 0))
        if side_excl_l > 0:
            mask[:int(top_excl * 2.5), :min(side_excl_l, mask.shape[1])] = 0
        side_excl_r = int(cfg.get("right_ui_exclude_px", 0))
        if side_excl_r > 0:
            cut = max(0, mask.shape[1] - side_excl_r)
            mask[:int(top_excl * 2.5), cut:] = 0
        # Bottom UI strip — the chat/clan-castle/Surrender buttons can
        # bleed warm-coloured pixels into the bottom of the playfield
        # ROI. Mask the last `bottom_ui_exclude_px` rows above ui_cutoff.
        bottom_excl = int(cfg.get("bottom_ui_exclude_px", 0))
        if bottom_excl > 0:
            cut = max(0, mask.shape[0] - bottom_excl)
            mask[cut:, :] = 0

        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )
        if not contours:
            log.debug("RedZone (%s): no contours.", mode)
            return None

        playfield_area = float(w * ui_cutoff)
        min_area_ratio = float(cfg.get("min_polygon_area_ratio", 0.10))
        max_area_ratio = float(cfg.get("max_polygon_area_ratio", 0.90))
        min_area = max(2000.0, playfield_area * min_area_ratio)
        max_area = playfield_area * max_area_ratio
        eps = float(cfg.get("approx_eps_px", 2.0))

        # ── Attempt 1: largest single contour ────────────────────
        best = max(contours, key=cv2.contourArea)
        verts = self._verts_from_contour(best, eps)
        if verts is not None:
            best_area = float(cv2.contourArea(best))
            if min_area <= best_area <= max_area and \
                    self._sanity_ok(verts, w, ui_cutoff, cfg, mode) and \
                    self._candidate_covers_guard(verts, guard, w, cfg):
                self._log_polygon(mode, verts, best_area, "single")
                self._maybe_dump(
                    screenshot, mask, contours, mode, cfg,
                    verts=verts, source="single",
                )
                return verts
            elif best_area < min_area:
                log.debug("RedZone (%s) single: area %.0f < min %.0f.",
                          mode, best_area, min_area)
            elif best_area > max_area:
                log.debug("RedZone (%s) single: area %.0f > max %.0f (UI?).",
                          mode, best_area, max_area)
            elif guard is not None:
                log.info(
                    "RedZone (%s) single rejected: polygon does not cover "
                    "the YOLO base guard.", mode,
                )

        # ── Attempt 2: fuse the top-K contours into one hull ─────
        # Real perimeters are often broken into 2-4 pieces when the
        # morph close can't bridge the gap. Stacking them and taking the
        # combined convex hull recovers the true outline.
        fuse_k = int(cfg.get("fuse_top_k", 5))
        fuse_min_each = max(200.0, min_area * 0.05)
        ranked = sorted(
            (c for c in contours if cv2.contourArea(c) >= fuse_min_each),
            key=cv2.contourArea, reverse=True,
        )[:max(2, fuse_k)]
        if len(ranked) >= 2:
            stacked = np.vstack(ranked)
            verts2 = self._verts_from_contour(stacked, eps)
            if verts2 is not None:
                fused_area = float(cv2.contourArea(cv2.convexHull(stacked)))
                if min_area <= fused_area <= max_area and \
                        self._sanity_ok(verts2, w, ui_cutoff, cfg, mode) and \
                        self._candidate_covers_guard(verts2, guard, w, cfg):
                    self._log_polygon(mode, verts2, fused_area,
                                      f"fused-{len(ranked)}")
                    self._maybe_dump(
                        screenshot, mask, contours, mode, cfg,
                        verts=verts2, source=f"fused-{len(ranked)}",
                    )
                    return verts2
                else:
                    log.debug(
                        "RedZone (%s) fused-%d: area=%.0f sanity FAIL.",
                        mode, len(ranked), fused_area,
                    )

        # Both attempts failed — optionally dump a debug overlay so the
        # user can see what we caught.
        self._maybe_dump(screenshot, mask, contours, mode, cfg)
        return None

    @classmethod
    def _detect_yolo_guard(
        cls,
        screenshot: np.ndarray,
        ui_cutoff: int,
        cfg: dict,
    ) -> Optional[np.ndarray]:
        """Return the model's BaseArea hull, only to reject clipped redlines."""
        model_path = str(
            cfg.get("yolo_guard_model", "assets/models/coc_deployable_seg.pt")
        )
        if not os.path.isabs(model_path):
            project_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", ".."),
            )
            model_path = os.path.join(project_root, model_path)
        model_path = os.path.abspath(model_path)

        if model_path in cls._yolo_failed_paths:
            return None
        if not os.path.isfile(model_path):
            log.warning("RedZone YOLO guard model not found: %s", model_path)
            cls._yolo_failed_paths.add(model_path)
            return None

        try:
            if cls._yolo_model is None or cls._yolo_model_path != model_path:
                from ultralytics import YOLO
                cls._yolo_model = YOLO(model_path)
                cls._yolo_model_path = model_path
                log.info("RedZone YOLO guard loaded: %s", model_path)

            confidence = float(cfg.get("yolo_guard_confidence", 0.25))
            image_size = max(320, int(cfg.get("yolo_guard_imgsz", 480)))
            result = cls._yolo_model.predict(
                screenshot,
                conf=confidence,
                imgsz=image_size,
                device="cpu",
                max_det=5,
                retina_masks=True,
                verbose=False,
            )[0]
            if result.boxes is None or result.masks is None:
                log.info("RedZone YOLO guard: no BaseArea mask detected.")
                return None

            names = result.names or {}
            choices = [
                index for index, class_id in enumerate(result.boxes.cls)
                if str(names.get(int(class_id), "")).lower() == "basearea"
            ]
            if not choices:
                log.info("RedZone YOLO guard: prediction has no BaseArea class.")
                return None
            best = max(choices, key=lambda i: float(result.boxes.conf[i]))
            score = float(result.boxes.conf[best])

            h, w = screenshot.shape[:2]
            mask = np.zeros((h, w), dtype=np.uint8)
            xy = np.rint(result.masks.xy[best]).astype(np.int32)
            if len(xy) < 3:
                return None
            cv2.fillPoly(mask, [xy], 255)
            mask[ui_cutoff:, :] = 0

            kernel_px = max(
                3,
                int(round(float(cfg.get("yolo_guard_open_kernel_px", 19))
                          * w / 1350.0)),
            )
            if kernel_px % 2 == 0:
                kernel_px += 1
            kernel = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (kernel_px, kernel_px),
            )
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

            count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
            if count <= 1:
                return None
            component = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            mask = np.where(labels == component, 255, 0).astype(np.uint8)
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
            )
            if not contours:
                return None
            hull = cv2.convexHull(max(contours, key=cv2.contourArea))
            eps = max(
                1.0,
                float(cfg.get("approx_eps_px", 2.0)) * w / 1350.0,
            )
            guard = cv2.approxPolyDP(hull, eps, True).reshape(-1, 2)

            guard_area = float(cv2.contourArea(guard))
            playfield_area = float(w * ui_cutoff)
            if (len(guard) < 4 or guard_area < playfield_area * 0.04
                    or guard_area > playfield_area * 0.75):
                log.info(
                    "RedZone YOLO guard rejected by size: verts=%d area=%.0f",
                    len(guard), guard_area,
                )
                return None

            cls._dump_yolo_guard(screenshot, mask, guard, cfg, score)
            log.info(
                "RedZone YOLO guard: confidence=%.3f verts=%d area=%.0f",
                score, len(guard), guard_area,
            )
            return guard.astype(np.int32)
        except Exception as exc:
            log.warning("RedZone YOLO guard disabled after error: %s", exc)
            cls._yolo_failed_paths.add(model_path)
            cls._yolo_model = None
            cls._yolo_model_path = ""
            return None

    @staticmethod
    def _candidate_covers_guard(
        candidate: np.ndarray,
        guard: np.ndarray | None,
        screen_w: int,
        cfg: dict,
    ) -> bool:
        if guard is None or len(guard) < 3:
            return True
        tolerance = max(
            0.0,
            float(cfg.get("yolo_guard_tolerance_px", 8)) * screen_w / 1350.0,
        )
        covered = sum(
            cv2.pointPolygonTest(
                candidate, (float(point[0]), float(point[1])), True,
            ) >= -tolerance
            for point in guard.reshape(-1, 2)
        )
        ratio = covered / float(len(guard))
        required = min(
            1.0,
            max(0.5, float(cfg.get("yolo_guard_min_coverage", 0.85))),
        )
        if ratio < required:
            log.info(
                "RedZone candidate covers only %.0f%% of YOLO guard (need %.0f%%).",
                ratio * 100.0, required * 100.0,
            )
            return False
        return True

    @staticmethod
    def _dump_yolo_guard(
        screenshot: np.ndarray,
        mask: np.ndarray,
        guard: np.ndarray,
        cfg: dict,
        score: float,
    ) -> None:
        out_dir = cfg.get("debug_dump") or ""
        if not out_dir:
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            overlay = screenshot.copy()
            tint = np.zeros_like(overlay)
            tint[:, :, 0] = mask
            tint[:, :, 2] = mask
            overlay = cv2.addWeighted(overlay, 0.75, tint, 0.35, 0)
            cv2.polylines(
                overlay, [guard.reshape(-1, 1, 2)], True, (0, 255, 255), 3,
            )
            ts = int(time.time() * 1000)
            path = os.path.join(
                out_dir, f"yolo_guard_success_{score:.3f}_{ts}.png",
            )
            cv2.imwrite(path, overlay)
            log.info("RedZone YOLO guard debug dump → %s", path)
        except Exception as exc:
            log.debug("RedZone YOLO guard dump failed: %s", exc)

    @staticmethod
    def _verts_from_contour(
        contour: np.ndarray, eps: float,
    ) -> Optional[np.ndarray]:
        if contour is None or len(contour) < 3:
            return None
        hull = cv2.convexHull(contour)
        approx = cv2.approxPolyDP(hull, eps, True)
        if approx is None or len(approx) < 4:
            return None
        return approx.reshape(-1, 2).astype(np.int32)

    @staticmethod
    def _log_polygon(
        mode: str, verts: np.ndarray, area: float, source: str,
    ) -> None:
        x_min, y_min = verts.min(axis=0)
        x_max, y_max = verts.max(axis=0)
        bw, bh = int(x_max - x_min), int(y_max - y_min)
        log.info(
            "RedZone polygon (%s/%s): %d verts, bbox=(%d,%d,%d,%d), area=%.0f",
            mode, source, len(verts), int(x_min), int(y_min), bw, bh, area,
        )

    @staticmethod
    def _sanity_ok(
        verts: np.ndarray, w: int, ui_cutoff: int, cfg: dict, mode: str,
    ) -> bool:
        x_min, y_min = verts.min(axis=0)
        x_max, y_max = verts.max(axis=0)
        bw, bh = int(x_max - x_min), int(y_max - y_min)

        if bw > w * 0.97 and bh > ui_cutoff * 0.97:
            log.debug("RedZone (%s) sanity FAIL: covers entire playfield.", mode)
            return False

        # Polygon must NOT touch the top of the screen — if it does, the
        # mask was contaminated by the timer banner / loot HUD.
        min_top_y = int(cfg.get("min_polygon_y_px", 60))
        if y_min < min_top_y:
            log.debug("RedZone (%s) sanity FAIL: top y_min=%d < %d (UI contamination).",
                      mode, y_min, min_top_y)
            return False

        # Aspect ratio sanity — a real CoC base is roughly square-ish at
        # zoom-out (0.45 ≤ ratio ≤ 2.2). Strips that look like 5:1 or
        # 1:5 are almost always UI strips, not bases.
        if bw <= 0 or bh <= 0:
            return False
        ratio = bw / max(1, bh)
        if ratio < 0.40 or ratio > 2.5:
            log.debug("RedZone (%s) sanity FAIL: aspect ratio %.2f out of range.",
                      mode, ratio)
            return False

        # Minimum base width. The configured value is an ABSOLUTE pixel
        # count tuned on a 1920-wide screen (500px ≈ 26% of the width). On a
        # narrower panel such as 1350x1080 that same 500px would demand 37%
        # of the screen and reject perfectly valid bases, so scale the gate
        # down proportionally. Screens at/above 1920 keep the exact old
        # threshold.
        min_w_cfg = int(cfg.get("min_polygon_width_px", 500))
        width_ratio = float(cfg.get("min_polygon_width_ratio", 500.0 / 1920.0))
        min_w = min(min_w_cfg, int(w * width_ratio))
        if bw < min_w:
            log.debug("RedZone (%s) sanity FAIL: width %d < %d.", mode, bw, min_w)
            return False

        return True

    @staticmethod
    def _build_mask(roi: np.ndarray, cfg: dict) -> np.ndarray:
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        # Stricter saturation (was 100) keeps semi-transparent HUD
        # overlays out of the mask. The actual perimeter dashes hit
        # S ≥ 180 reliably across themes.
        s_min = int(cfg.get("hsv_s_min", 150))
        v_min = int(cfg.get("hsv_v_min", 110))
        m_red_low  = cv2.inRange(hsv, np.array([0,   s_min, v_min]), np.array([12,  255, 255]))
        m_red_high = cv2.inRange(hsv, np.array([168, s_min, v_min]), np.array([180, 255, 255]))
        m_orange   = cv2.inRange(hsv, np.array([10,  s_min, v_min]), np.array([24,  255, 255]))
        # Pink/magenta dashes appear on lava themes. Saturation eased
        # because pink reads as desaturated red on emulator screencaps.
        m_pink     = cv2.inRange(hsv, np.array([140, 110, v_min]), np.array([170, 220, 255]))
        m_magenta  = cv2.inRange(hsv, np.array([150, 130, v_min]), np.array([175, 255, 255]))
        mask = m_red_low | m_red_high | m_orange | m_pink | m_magenta

        kh = tuple(cfg.get("morph_close_h_kernel", [35, 3]))
        kv = tuple(cfg.get("morph_close_v_kernel", [3, 35]))
        ks = tuple(cfg.get("morph_close_square", [9, 9]))
        kh_el = cv2.getStructuringElement(cv2.MORPH_RECT, kh)
        kv_el = cv2.getStructuringElement(cv2.MORPH_RECT, kv)
        ks_el = cv2.getStructuringElement(cv2.MORPH_RECT, ks)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kh_el, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kv_el, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, ks_el, iterations=1)
        return mask

    @staticmethod
    def _build_mask_inverted(roi_inv: np.ndarray, cfg: dict) -> np.ndarray:
        """After ``cv2.bitwise_not``, the perimeter dashes (originally
        red H≈0/180) move to cyan (H≈90). Detect THAT range instead.
        Same morph closing, same kernel sizes — only the colour band
        changes."""
        hsv = cv2.cvtColor(roi_inv, cv2.COLOR_BGR2HSV)
        s_min = int(cfg.get("hsv_s_min", 150))
        v_min = int(cfg.get("hsv_v_min", 110))
        m_cyan = cv2.inRange(hsv, np.array([78,  s_min, v_min]), np.array([102, 255, 255]))
        m_blue = cv2.inRange(hsv, np.array([95,  s_min, v_min]), np.array([115, 255, 255]))
        # Inverted orange becomes light teal/blue.
        m_teal = cv2.inRange(hsv, np.array([85,  120, v_min]), np.array([100, 255, 255]))
        mask = m_cyan | m_blue | m_teal

        kh = tuple(cfg.get("morph_close_h_kernel", [35, 3]))
        kv = tuple(cfg.get("morph_close_v_kernel", [3, 35]))
        ks = tuple(cfg.get("morph_close_square", [9, 9]))
        kh_el = cv2.getStructuringElement(cv2.MORPH_RECT, kh)
        kv_el = cv2.getStructuringElement(cv2.MORPH_RECT, kv)
        ks_el = cv2.getStructuringElement(cv2.MORPH_RECT, ks)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kh_el, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kv_el, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, ks_el, iterations=1)
        return mask

    @staticmethod
    def _maybe_dump(
        screenshot: np.ndarray,
        mask: np.ndarray,
        contours: List[np.ndarray],
        mode: str,
        cfg: dict,
        verts: np.ndarray | None = None,
        source: str = "failed",
    ) -> None:
        """Save an overlay showing the mask, contours and chosen polygon."""
        out_dir = cfg.get("debug_dump") or ""
        if not out_dir:
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            overlay = screenshot.copy()
            roi_h = mask.shape[0]
            # Tint mask onto the playfield region in red.
            tint = np.zeros_like(overlay)
            tint[:roi_h, :, 2] = mask  # R channel
            overlay = cv2.addWeighted(overlay, 0.7, tint, 0.5, 0)
            # Draw every found contour in cyan so fragmented detections
            # are visible.
            for c in contours[:8]:
                cv2.drawContours(overlay, [c], -1, (255, 255, 0), 2)
            # Draw the largest hull in green for emphasis.
            if contours:
                top = max(contours, key=cv2.contourArea)
                cv2.drawContours(overlay, [cv2.convexHull(top)], -1,
                                 (0, 255, 0), 3)
            if verts is not None:
                polygon = verts.reshape((-1, 1, 2))
                cv2.polylines(overlay, [polygon], True, (0, 255, 255), 4)
                center = RedZonePolygonSkill.centroid(verts)
                if center is not None:
                    cv2.circle(overlay, center, 10, (255, 0, 255), -1)
            ts = int(time.time() * 1000)
            status = "success" if verts is not None else "failed"
            path = os.path.join(
                out_dir, f"redzone_{status}_{mode}_{source}_{ts}.png",
            )
            cv2.imwrite(path, overlay)
            log.info("RedZone debug dump → %s", path)
        except Exception as exc:
            log.debug("RedZone debug dump failed: %s", exc)

    @staticmethod
    def dump_ring_plan(
        screenshot: np.ndarray,
        outer_polygon: np.ndarray,
        inner_polygon: np.ndarray | None,
        ring: list[tuple[int, int]],
        drops: list[tuple[int, int]],
        config: dict,
        troop: str,
    ) -> None:
        """Save the corridor and exact Ring Sweep points for review."""
        out_dir = ((config or {}).get("polygon", {}) or {}).get("debug_dump") or ""
        if not out_dir or screenshot is None or inner_polygon is None:
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            overlay = screenshot.copy()
            outer = outer_polygon.reshape((-1, 1, 2)).astype(np.int32)
            inner = inner_polygon.reshape((-1, 1, 2)).astype(np.int32)
            corridor = np.zeros_like(overlay)
            cv2.fillPoly(corridor, [outer], (0, 180, 0))
            cv2.fillPoly(corridor, [inner], (0, 0, 0))
            overlay = cv2.addWeighted(overlay, 0.78, corridor, 0.35, 0)
            cv2.polylines(overlay, [outer], True, (0, 255, 255), 3)
            cv2.polylines(overlay, [inner], True, (255, 255, 0), 3)
            if ring:
                for point in ring:
                    cv2.circle(overlay, point, 4, (255, 0, 0), -1)
            for point in drops:
                cv2.circle(overlay, point, 10, (0, 255, 0), -1)
            safe_troop = "".join(
                c for c in str(troop) if c.isalnum() or c in "-_"
            )
            ts = int(time.time() * 1000)
            path = os.path.join(out_dir, f"ringsweep_{safe_troop}_{ts}.png")
            cv2.imwrite(path, overlay)
            log.info("Ring Sweep debug dump → %s", path)
        except Exception as exc:
            log.debug("Ring Sweep debug dump failed: %s", exc)

    @staticmethod
    def dump_spell_plan(
        screenshot: np.ndarray,
        base_polygon: np.ndarray | None,
        drops: list[tuple[int, int]],
        drops_per_wave: int,
        config: dict,
        spell: str,
    ) -> None:
        """Save the planned spell carpet: every drop, coloured by wave.

        Mirrors ``dump_ring_plan`` for troops. The number drawn on each dot
        is the wave it belongs to, so you can confirm the 5-per-wave spread
        matches the preview on a real capture.
        """
        out_dir = ((config or {}).get("polygon", {}) or {}).get("debug_dump") or ""
        if not out_dir or screenshot is None or not drops:
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            overlay = screenshot.copy()
            if base_polygon is not None and len(base_polygon) >= 3:
                base = base_polygon.reshape((-1, 1, 2)).astype(np.int32)
                shade = np.zeros_like(overlay)
                cv2.fillPoly(shade, [base], (0, 120, 0))
                overlay = cv2.addWeighted(overlay, 0.8, shade, 0.25, 0)
                cv2.polylines(overlay, [base], True, (255, 255, 0), 2)
            # One colour per wave, cycling if there are more than six.
            wave_colours = [
                (60, 60, 255), (0, 180, 255), (0, 255, 255),
                (0, 255, 60), (255, 180, 0), (255, 60, 180),
            ]
            per_wave = max(1, int(drops_per_wave))
            for index, point in enumerate(drops):
                wave = index // per_wave
                colour = wave_colours[wave % len(wave_colours)]
                cv2.circle(overlay, (int(point[0]), int(point[1])), 9, colour, -1)
                cv2.putText(
                    overlay, str(wave + 1),
                    (int(point[0]) - 5, int(point[1]) + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA,
                )
            safe = "".join(c for c in str(spell) if c.isalnum() or c in "-_")
            ts = int(time.time() * 1000)
            path = os.path.join(out_dir, f"spellcarpet_{safe}_{ts}.png")
            cv2.imwrite(path, overlay)
            log.info("Spell carpet debug dump → %s", path)
        except Exception as exc:
            log.debug("Spell carpet dump failed: %s", exc)

    @staticmethod
    def is_inside(polygon: np.ndarray, x: int, y: int, margin: int = 0) -> bool:
        """Point-in-polygon with optional inflation margin (px)."""
        if polygon is None or len(polygon) < 3:
            return False
        if margin == 0:
            return cv2.pointPolygonTest(polygon, (float(x), float(y)), False) >= 0
        dist = cv2.pointPolygonTest(polygon, (float(x), float(y)), True)
        return dist >= -float(margin)

    @staticmethod
    def bbox(polygon: np.ndarray) -> tuple[int, int, int, int] | None:
        if polygon is None or len(polygon) == 0:
            return None
        x, y, w, h = cv2.boundingRect(polygon)
        return int(x), int(y), int(w), int(h)

    @staticmethod
    def centroid(polygon: np.ndarray) -> tuple[int, int] | None:
        if polygon is None or len(polygon) == 0:
            return None
        m = cv2.moments(polygon)
        if abs(m["m00"]) < 1e-3:
            x, y, w, h = cv2.boundingRect(polygon)
            return int(x + w / 2), int(y + h / 2)
        return int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])
