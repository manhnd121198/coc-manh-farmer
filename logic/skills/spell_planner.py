"""
SpellPlannerSkill — chooses a drop coordinate for each selected spell.

Inputs:
    cluster_xy   — main army cluster (deploy anchor).
    target_xy    — base centroid OR specific target (TH / Inferno / ...).
    spell_name   — selected spell key (rage_spell, freeze_spell, ...).
    config       — v2_attack_rules + v2_spell_profiles.
    target_locator — used to find on-screen defenses for spell-on-target
                     placements.

Behaviour by placement:
    "ahead"     → along (cluster→target) at config.path_fraction; with
                  drop_count > 1 the drops step forward by path_fraction_step.
    "on_army"   → near cluster (small jitter).
    "on_target" → on the closest match of target_priority assets.
    "on_wall"   → middle of the cluster→target line (jump/earthquake).
    "inside_base_random" → random points safely inside the enemy polygon.
    "inside_base_ring" → scattered points around an interior ring.
    "inside_base_uniform" → ``drop_count`` points spread evenly across the
                  base interior (farthest-point order), for carpeting the
                  whole base with many spells over several waves.
"""

from __future__ import annotations

import math
import random
from typing import List, Optional, Tuple

import numpy as np

from core.logger import BotLogger
from vision.skills.red_zone_polygon import RedZonePolygonSkill
from vision.skills.target_locator import TargetLocatorSkill

log = BotLogger.get("v2.spell_planner")


class SpellPlannerSkill:
    name = "spell_planner"

    def __init__(self, target_locator: TargetLocatorSkill) -> None:
        self._tl = target_locator

    def plan_spell(
        self,
        screenshot: np.ndarray,
        spell_name: str,
        cluster_xy: tuple[int, int],
        target_xy: tuple[int, int],
        config: dict | None = None,
        spell_profiles: dict | None = None,
        base_polygon: np.ndarray | None = None,
    ) -> List[Tuple[int, int]]:
        cfg = config or {}
        profiles = spell_profiles or {}
        profile = profiles.get(spell_name) or self._guess_profile(spell_name, cfg)
        if profile is None:
            return [self._on_path(cluster_xy, target_xy, 0.55)]

        placement = profile.get("placement", "ahead")
        drop_count = max(1, int(profile.get("drop_count", 1)))
        out: List[Tuple[int, int]] = []

        if placement == "ahead":
            frac = float(profile.get("path_fraction", 0.65))
            # Several rages stacked on one spot waste each other — the radii
            # overlap. Spread them forward along the push line instead, so
            # the army keeps running through fresh ones as it advances.
            spread = float(profile.get("path_fraction_step", 0.10))
            for i in range(drop_count):
                step = min(0.95, frac + spread * i)
                px, py = self._on_path(cluster_xy, target_xy, step)
                if i == 0:
                    out.append((px, py))
                else:
                    out.append((px + random.randint(-15, 15),
                                py + random.randint(-15, 15)))

        elif placement == "on_army":
            # The deployment cluster is normally outside the base.  Move
            # toward the target before dropping spells so taps land on the
            # battlefield instead of the troop deployment edge.
            frac = float(profile.get("path_fraction", 0.35))
            cx, cy = self._on_path(cluster_xy, target_xy, frac)
            for _ in range(drop_count):
                out.append((cx + random.randint(-30, 30), cy + random.randint(-30, 30)))

        elif placement == "on_target":
            priority = profile.get("target_priority", []) or []
            hit = self._tl.find_first_of(screenshot, priority)
            if hit is not None:
                _, hx, hy = hit
                for i in range(drop_count):
                    jitter = 25 + 15 * i
                    out.append((hx + random.randint(-jitter, jitter),
                                hy + random.randint(-jitter, jitter)))
            else:
                frac = float(profile.get("path_fraction", 0.55))
                out.append(self._on_path(cluster_xy, target_xy, frac))

        elif placement == "on_wall":
            frac = float(profile.get("path_fraction", 0.45))
            base_pt = self._on_path(cluster_xy, target_xy, frac)
            for i in range(drop_count):
                bx, by = base_pt
                out.append((bx + random.randint(-20, 20), by + random.randint(-20, 20)))

        elif placement == "inside_base_random":
            inner_scale = float(profile.get("inner_scale", 0.80))
            out.extend(self._random_inside_base(
                base_polygon, target_xy, drop_count, inner_scale,
            ))
            if not out:
                out.append(self._on_path(cluster_xy, target_xy, 0.80))

        elif placement == "inside_base_ring":
            out.extend(self._random_inside_ring(
                base_polygon, target_xy, drop_count, profile,
            ))
            if not out:
                out.append(self._on_path(cluster_xy, target_xy, 0.80))

        elif placement == "inside_base_uniform":
            inner_scale = float(profile.get("inner_scale", 0.85))
            out.extend(self._uniform_inside_base(
                base_polygon, target_xy, drop_count, inner_scale,
            ))
            if not out:
                out.append(self._on_path(cluster_xy, target_xy, 0.80))

        elif placement == "advancing_line":
            drops_per_wave = max(1, int(profile.get("drops_per_wave", 5)))
            # spacing_px is the spell footprint on a 1350px capture; scale it
            # to the actual screen so drops keep one spell-width apart.
            screen_w = screenshot.shape[1] if screenshot is not None else 1350
            spacing_px = float(profile.get("spacing_px", 120)) * screen_w / 1350.0
            out.extend(self._advancing_line(
                cluster_xy, target_xy, drop_count, drops_per_wave,
                float(profile.get("start_fraction", 0.35)),
                spacing_px,
                float(profile.get("line_spread", 0.9)),
                base_polygon,
            ))
            if not out:
                out.append(self._on_path(cluster_xy, target_xy, 0.55))

        else:
            out.append(self._on_path(cluster_xy, target_xy, 0.55))

        return out

    @staticmethod
    def _random_inside_base(
        polygon: np.ndarray | None,
        target_xy: tuple[int, int],
        count: int,
        inner_scale: float,
    ) -> List[Tuple[int, int]]:
        bbox = RedZonePolygonSkill.bbox(polygon)
        if bbox is None:
            return []

        x, y, w, h = bbox
        center = RedZonePolygonSkill.centroid(polygon) or target_xy
        cx, cy = center
        scale = min(1.0, max(0.10, inner_scale))
        points: List[Tuple[int, int]] = []
        max_attempts = max(100, count * 50)

        for _ in range(max_attempts):
            raw_x = random.randint(x, x + w - 1)
            raw_y = random.randint(y, y + h - 1)
            if not RedZonePolygonSkill.is_inside(polygon, raw_x, raw_y):
                continue
            px = int(round(cx + (raw_x - cx) * scale))
            py = int(round(cy + (raw_y - cy) * scale))
            if RedZonePolygonSkill.is_inside(polygon, px, py):
                points.append((px, py))
                if len(points) == count:
                    break

        return points

    @staticmethod
    def _uniform_inside_base(
        polygon: np.ndarray | None,
        target_xy: tuple[int, int],
        count: int,
        inner_scale: float,
    ) -> List[Tuple[int, int]]:
        """``count`` points spread as evenly as possible inside the base.

        Used to carpet the base with spells (e.g. 30 Rage / 30 Heal). The
        points come out ordered by farthest-point sampling: the first few
        already span the whole base and each later one fills the biggest
        remaining gap. Delivered in waves (``drops_per_wave``), that means
        every wave lands broadly rather than clustering in one corner, and
        the coverage only gets denser as more waves go down.
        """
        bbox = RedZonePolygonSkill.bbox(polygon)
        if bbox is None or count <= 0:
            return []

        x, y, w, h = bbox
        center = RedZonePolygonSkill.centroid(polygon) or target_xy
        cx, cy = center
        scale = min(1.0, max(0.10, inner_scale))

        # Dense candidate grid, shrunk toward the centre so drops land on
        # buildings rather than the base's grassy rim.
        step = max(6, int(round(min(w, h) / 20.0)))
        candidates: List[Tuple[int, int]] = []
        for gx in range(x, x + w + 1, step):
            for gy in range(y, y + h + 1, step):
                px = int(round(cx + (gx - cx) * scale))
                py = int(round(cy + (gy - cy) * scale))
                if RedZonePolygonSkill.is_inside(polygon, px, py):
                    candidates.append((px, py))

        if not candidates:
            return []
        if len(candidates) <= count:
            return candidates

        # Farthest-point sampling. Seed from the point nearest the centroid
        # so the first drop is central, then repeatedly take the candidate
        # furthest from everything chosen so far.
        seed = min(
            range(len(candidates)),
            key=lambda i: (candidates[i][0] - cx) ** 2 + (candidates[i][1] - cy) ** 2,
        )
        chosen: List[Tuple[int, int]] = [candidates[seed]]
        nearest = [
            (p[0] - candidates[seed][0]) ** 2 + (p[1] - candidates[seed][1]) ** 2
            for p in candidates
        ]
        while len(chosen) < count:
            pick = max(range(len(candidates)), key=lambda i: nearest[i])
            if nearest[pick] <= 0:
                break                                   # ran out of distinct spots
            px, py = candidates[pick]
            chosen.append((px, py))
            for i, p in enumerate(candidates):
                d = (p[0] - px) ** 2 + (p[1] - py) ** 2
                if d < nearest[i]:
                    nearest[i] = d
        return chosen

    @staticmethod
    def _advancing_line(
        cluster_xy: tuple[int, int],
        target_xy: tuple[int, int],
        count: int,
        drops_per_wave: int,
        start_fraction: float,
        spacing_px: float,
        line_spread: float,
        polygon: np.ndarray | None,
    ) -> List[Tuple[int, int]]:
        """Rolling-barrage points spaced so spell radii don't overlap.

        ``spacing_px`` is the centre-to-centre gap = the spell footprint. It
        drives BOTH the gap between drops along a line AND how far the front
        advances between waves, so no two drops land on top of each other.

        Returns points in WAVE-MAJOR order (line 1, then line 2, …). A line
        never holds more drops than fit across the base at this spacing, and
        waves stop once the front passes the far edge of the base — so on a
        small base it uses FEWER than ``count`` drops rather than piling them
        up. Widen coverage by lowering ``spacing_px`` (more, closer drops) or
        raising it (fewer, cleaner spacing).

        The front is a pure time/geometry estimate of where the army is; it
        does not look at the troops themselves.
        """
        if count <= 0:
            return []
        cx, cy = cluster_xy
        tx, ty = target_xy
        dx, dy = tx - cx, ty - cy
        dist = math.hypot(dx, dy)
        if dist < 1.0:
            return []
        ux, uy = dx / dist, dy / dist            # advance direction
        perp_x, perp_y = -uy, ux                 # line direction (perpendicular)
        spacing = max(8.0, float(spacing_px))
        per_wave = max(1, int(drops_per_wave))

        # Base extents relative to the deploy point: how far the front may
        # advance (along the push) and how wide a line may be (perpendicular).
        half_len = dist * 0.45
        max_reach = dist * 1.6
        if polygon is not None and len(polygon) >= 3:
            verts = polygon.reshape(-1, 2).astype(float)
            along = [(vx - cx) * ux + (vy - cy) * uy for vx, vy in verts]
            across = [(vx - cx) * perp_x + (vy - cy) * perp_y for vx, vy in verts]
            if along:
                max_reach = max(along)
            if across:
                half_len = max(20.0, (max(across) - min(across)) / 2.0)
        half_len *= max(0.1, min(1.0, line_spread))

        # How many drops fit across a line at this spacing without touching —
        # never more than the wave budget.
        fit_perp = int((2.0 * half_len) / spacing) + 1
        per_line = max(1, min(per_wave, fit_perp))

        start_offset = dist * start_fraction
        num_waves = max(1, math.ceil(count / per_line))

        out: List[Tuple[int, int]] = []
        for wave in range(num_waves):
            front_off = start_offset + spacing * wave
            if front_off > max_reach:            # front passed the far edge
                break
            fx = cx + ux * front_off
            fy = cy + uy * front_off
            n = min(per_line, count - len(out))
            for i in range(n):
                t = i - (n - 1) / 2.0            # centred, spacing apart
                ox, oy = perp_x * spacing * t, perp_y * spacing * t
                sx = fx + ox + random.randint(-5, 5)
                sy = fy + oy + random.randint(-5, 5)
                # Keep the drop on the base: if it spilled outside, pull it
                # back toward the front centre until it lands inside.
                if polygon is not None and not RedZonePolygonSkill.is_inside(
                    polygon, int(round(sx)), int(round(sy)),
                ):
                    for shrink in (0.7, 0.45, 0.2, 0.0):
                        sx = fx + ox * shrink
                        sy = fy + oy * shrink
                        if RedZonePolygonSkill.is_inside(
                            polygon, int(round(sx)), int(round(sy)),
                        ):
                            break
                cand = (int(round(sx)), int(round(sy)))
                # Enforce the footprint gap even after the inside-base pull:
                # drop this point if it crowded an already-placed one.
                min_gap_sq = (spacing * 0.6) ** 2
                if any((cand[0] - qx) ** 2 + (cand[1] - qy) ** 2 < min_gap_sq
                       for qx, qy in out):
                    continue
                out.append(cand)
                if len(out) >= count:
                    break
            if len(out) >= count:
                break
        return out

    @staticmethod
    def _random_inside_ring(
        polygon: np.ndarray | None,
        target_xy: tuple[int, int],
        count: int,
        profile: dict,
    ) -> List[Tuple[int, int]]:
        bbox = RedZonePolygonSkill.bbox(polygon)
        if bbox is None:
            return []

        _x, _y, w, h = bbox
        center = RedZonePolygonSkill.centroid(polygon) or target_xy
        cx, cy = center
        drops_per_wave = max(1, int(profile.get("drops_per_wave", count)))
        radius_min = min(0.95, max(0.10, float(profile.get("ring_radius_min", 0.65))))
        radius_max = min(0.98, max(radius_min, float(profile.get("ring_radius_max", 0.85))))
        max_ray = math.hypot(w, h)
        angles: List[float] = []
        wave_rotation = 0.0

        for index in range(count):
            wave_slot = index % drops_per_wave
            if wave_slot == 0:
                wave_rotation = random.uniform(0.0, 2.0 * math.pi)
            remaining = count - (index // drops_per_wave) * drops_per_wave
            slots_in_wave = min(drops_per_wave, remaining)
            angle_step = 2.0 * math.pi / slots_in_wave
            angle = (
                wave_rotation
                + wave_slot * angle_step
                + random.uniform(-0.25, 0.25) * angle_step
            )
            angles.append(angle)

        boundary_distances: List[float] = []
        for angle in angles:
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            lo, hi = 0.0, max_ray
            for _ in range(14):
                mid = (lo + hi) / 2.0
                mx = int(round(cx + cos_a * mid))
                my = int(round(cy + sin_a * mid))
                if RedZonePolygonSkill.is_inside(polygon, mx, my):
                    lo = mid
                else:
                    hi = mid
            boundary_distances.append(lo)

        if not boundary_distances:
            return []

        safe_radius = min(boundary_distances)
        points: List[Tuple[int, int]] = []
        for angle in angles:
            radius = safe_radius * random.uniform(radius_min, radius_max)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            px = int(round(cx + cos_a * radius))
            py = int(round(cy + sin_a * radius))
            if RedZonePolygonSkill.is_inside(polygon, px, py):
                points.append((px, py))

        return points

    @staticmethod
    def _on_path(
        cluster_xy: tuple[int, int],
        target_xy: tuple[int, int],
        frac: float,
    ) -> tuple[int, int]:
        cx, cy = cluster_xy
        tx, ty = target_xy
        return int(round(cx + (tx - cx) * frac)), int(round(cy + (ty - cy) * frac))

    @staticmethod
    def _guess_profile(spell_name: str, cfg: dict) -> Optional[dict]:
        path_fracs = cfg.get("spell_path_fractions", {}) or {}
        key = spell_name.lower()
        for prefix, value in path_fracs.items():
            if key == prefix or key.startswith(prefix + "_") or prefix in key:
                if isinstance(value, (int, float)):
                    return {"placement": "ahead", "path_fraction": float(value), "drop_count": 1}
                if isinstance(value, str):
                    if "inferno" in value or "eagle" in value:
                        return {
                            "placement": "on_target",
                            "target_priority": ["inferno_tower", "eagle_artillery"],
                            "drop_count": 1,
                        }
                    if "air_defense" in value:
                        return {
                            "placement": "on_target",
                            "target_priority": ["air_defense", "x_bow"],
                            "drop_count": 3,
                        }
                    if "wall" in value:
                        return {"placement": "on_wall", "path_fraction": 0.45, "drop_count": 1}
        return None
