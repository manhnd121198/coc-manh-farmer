"""
RingPlannerSkill — deploy points that hug the red zone instead of the
screen edge.

Why this exists
---------------
The original corridor model derives four axis-aligned rectangles from
the polygon's BOUNDING BOX and lets them run out to the screen edge.
Two things go wrong with that on a real base:

  1. A CoC base is a diamond/hexagon, so its usable rim runs DIAGONALLY.
     A diagonal rim lies *inside* the bounding box, and an axis-aligned
     rectangle placed *outside* that box can never contain it. Measured
     on one real frame: the hull covered 678k px, its bbox 892k px — 24 %
     of the box, the whole diagonal rim, was unreachable by the model.
  2. Whatever is left runs to the screen edge, which after a zoom-out is
     the forest border *outside* the playable map. Taps there are
     rejected with "You cannot deploy troops on the red area!".

This skill instead offsets the polygon itself outward by `offset_px` and
walks that contour. The result follows the diagonals automatically and
never strays further from the base than the offset, which is what a
human does: walk along the edge of the red zone.

Everything here is plain arithmetic — no cv2, no numpy — so the geometry
stays unit-testable without dragging the vision stack into the tests.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Sequence, Tuple

from core.logger import BotLogger

log = BotLogger.get("v2.ring_planner")

Point = Tuple[float, float]
IPoint = Tuple[int, int]

SIDES = ("left", "right", "top", "bottom")


class RingPlannerSkill:
    name = "ring_planner"

    # ── Public API ──────────────────────────────────────────────────

    @staticmethod
    def plan(
        polygon: Iterable[Sequence[float]],
        centroid: Sequence[float] | None = None,
        offset_px: int = 80,
        spacing_px: int = 45,
    ) -> Dict[str, List[IPoint]]:
        """Points spaced `spacing_px` apart along the polygon offset
        outward by `offset_px`, grouped by which side of the base they
        face. Sides with no points are omitted."""
        pts = RingPlannerSkill._normalise(polygon)
        if len(pts) < 3:
            return {}
        ring = RingPlannerSkill.offset(pts, offset_px)
        if len(ring) < 3:
            return {}
        walked = RingPlannerSkill.densify(ring, spacing_px)
        centre = tuple(float(v) for v in centroid) if centroid is not None \
            else RingPlannerSkill.centroid(pts)

        groups: Dict[str, List[IPoint]] = {}
        for (x, y) in walked:
            side = RingPlannerSkill.side_of((x, y), centre)
            groups.setdefault(side, []).append((int(round(x)), int(round(y))))
        return groups

    @staticmethod
    def offset(
        polygon: Iterable[Sequence[float]], offset_px: int,
    ) -> List[Point]:
        """Convex polygon pushed outward by `offset_px`.

        Each edge is moved along its outward normal and the new vertices
        are the intersections of consecutive moved edges — a true offset,
        not a scale about the centroid. Scaling would move the far ends
        of a wide base much further than its short sides, which is
        exactly the error that puts drops off the map.
        """
        pts = RingPlannerSkill._normalise(polygon)
        n = len(pts)
        if n < 3:
            return []
        if offset_px == 0:
            return pts
        centre = RingPlannerSkill.centroid(pts)

        # Moved edges, as (point_on_line, direction).
        lines: List[Tuple[Point, Point]] = []
        for i in range(n):
            p = pts[i]
            q = pts[(i + 1) % n]
            dx, dy = q[0] - p[0], q[1] - p[1]
            length = math.hypot(dx, dy)
            if length < 1e-6:
                lines.append((p, (1.0, 0.0)))
                continue
            ux, uy = dx / length, dy / length
            nx, ny = uy, -ux                      # one of the two normals
            mid = ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
            # Keep the normal that points AWAY from the centre.
            if (mid[0] + nx - centre[0]) ** 2 + (mid[1] + ny - centre[1]) ** 2 \
                    < (mid[0] - centre[0]) ** 2 + (mid[1] - centre[1]) ** 2:
                nx, ny = -nx, -ny
            moved = (p[0] + nx * offset_px, p[1] + ny * offset_px)
            lines.append((moved, (ux, uy)))

        # Vertex i is shared by edge i-1 and edge i, so the moved vertex
        # is where those two moved edges meet. Keeping that pairing means
        # out[i] is the offset of pts[i] — the caller can rely on the
        # order matching the input.
        out: List[Point] = []
        for i in range(n):
            a_pt, a_dir = lines[(i - 1) % n]
            b_pt, b_dir = lines[i]
            hit = RingPlannerSkill._intersect(a_pt, a_dir, b_pt, b_dir)
            if hit is None:
                # Collinear edges — the shared vertex just slides outward.
                hit = b_pt
            out.append(hit)
        return out

    @staticmethod
    def densify(ring: Sequence[Point], spacing_px: int) -> List[Point]:
        """Walk the closed contour, emitting a point every `spacing_px`."""
        step = max(4.0, float(spacing_px))
        pts = list(ring)
        if len(pts) < 2:
            return [tuple(p) for p in pts]

        out: List[Point] = []
        carry = 0.0
        for i in range(len(pts)):
            p = pts[i]
            q = pts[(i + 1) % len(pts)]
            seg = math.hypot(q[0] - p[0], q[1] - p[1])
            if seg < 1e-6:
                continue
            travelled = carry
            while travelled < seg:
                t = travelled / seg
                out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
                travelled += step
            carry = travelled - seg
        return out

    @staticmethod
    def side_of(point: Sequence[float], centroid: Sequence[float]) -> str:
        """Which face of the base a ring point belongs to. The dominant
        axis of the offset from the centre decides, so the diagonal rims
        split evenly between their two neighbouring sides instead of
        being dropped."""
        dx = float(point[0]) - float(centroid[0])
        dy = float(point[1]) - float(centroid[1])
        if abs(dx) >= abs(dy):
            return "right" if dx > 0 else "left"
        return "bottom" if dy > 0 else "top"

    @staticmethod
    def centroid(polygon: Iterable[Sequence[float]]) -> Point:
        pts = RingPlannerSkill._normalise(polygon)
        if not pts:
            return (0.0, 0.0)
        return (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )

    @staticmethod
    def sample(points: Sequence[IPoint], count: int) -> List[IPoint]:
        """`count` points spread evenly across `points`, order kept."""
        total = len(points)
        if total == 0 or count <= 0:
            return []
        if total <= count:
            return list(points)
        step = (total - 1) / float(count - 1) if count > 1 else 0.0
        return [points[int(round(i * step))] for i in range(count)]

    # ── Internals ───────────────────────────────────────────────────

    @staticmethod
    def _normalise(polygon: Iterable[Sequence[float]] | None) -> List[Point]:
        if polygon is None:
            return []
        out: List[Point] = []
        for vertex in polygon:
            v = list(vertex)
            # cv2 hulls come through as (1, 2) rows.
            if len(v) == 1:
                v = list(v[0])
            out.append((float(v[0]), float(v[1])))
        return out

    @staticmethod
    def _intersect(
        a_pt: Point, a_dir: Point, b_pt: Point, b_dir: Point,
    ) -> Point | None:
        cross = a_dir[0] * b_dir[1] - a_dir[1] * b_dir[0]
        if abs(cross) < 1e-9:
            return None
        dx = b_pt[0] - a_pt[0]
        dy = b_pt[1] - a_pt[1]
        t = (dx * b_dir[1] - dy * b_dir[0]) / cross
        return (a_pt[0] + a_dir[0] * t, a_pt[1] + a_dir[1] * t)
