"""Plan a closed deployment route around all four safe map edges."""

from __future__ import annotations

import random
from typing import Mapping, Sequence

Point = tuple[int, int]
Rect = tuple[int, int, int, int]


class PerimeterPlannerSkill:
    name = "perimeter_planner"

    @staticmethod
    def plan(corridors: Mapping[str, Rect], config: dict | None = None) -> list[Point]:
        """Return clockwise points on the centre lines of four safe corridors."""
        if not all(side in corridors for side in ("left", "right", "top", "bottom")):
            return []

        sweep = (config or {}).get("perimeter_sweep", {})
        points_per_side = max(1, int(sweep.get("points_per_side", 3)))
        polygon_cfg = (config or {}).get("polygon", {})
        top_safe_y = (
            int(polygon_cfg.get("top_ui_exclude_px", 0))
            + int((config or {}).get("tap_jitter_px", 0))
        )

        x_left, _ = PerimeterPlannerSkill._center(corridors["left"])
        x_right, _ = PerimeterPlannerSkill._center(corridors["right"])
        _, y_top = PerimeterPlannerSkill._center(corridors["top"])
        _, y_bottom = PerimeterPlannerSkill._center(corridors["bottom"])
        top_rect = corridors["top"]
        if top_safe_y > top_rect[1] + top_rect[3]:
            return []
        y_top = max(y_top, top_safe_y)
        if x_left >= x_right or y_top >= y_bottom:
            return []

        def interpolate(start: int, end: int, index: int) -> int:
            return int(round(start + (end - start) * index / points_per_side))

        route: list[Point] = []
        for i in range(points_per_side):
            route.append((interpolate(x_left, x_right, i), y_top))
        for i in range(points_per_side):
            route.append((x_right, interpolate(y_top, y_bottom, i)))
        for i in range(points_per_side):
            route.append((interpolate(x_right, x_left, i), y_bottom))
        for i in range(points_per_side):
            route.append((x_left, interpolate(y_bottom, y_top, i)))
        return route

    @staticmethod
    def randomize_route(points: Sequence[Point], rng=random) -> list[Point]:
        """Rotate the closed route to a random start and random direction."""
        if not points:
            return []
        start = rng.randrange(len(points))
        direction = rng.choice((1, -1))
        return [points[(start + direction * i) % len(points)] for i in range(len(points))]

    @staticmethod
    def _center(rect: Rect) -> Point:
        x, y, width, height = rect
        return int(x + width / 2), int(y + height / 2)
