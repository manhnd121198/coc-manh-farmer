"""Plan an evenly-spaced deploy ring inside the red-line corridor.

Why this exists alongside ``PerimeterPlannerSkill``
---------------------------------------------------
``PerimeterPlannerSkill`` walks the centre lines of the four *screen-edge*
corridors, and it bails out unless **all four** corridors exist. In practice
the corridor mapper only reports a side when there is enough empty screen
between the red-zone bbox and the screen border, so on most bases it returns
just ``{'left'}`` or ``{'left','right'}`` and the sweep never runs.

The detector returns the OUTER red boundary because OpenCV keeps the
largest external contour. The grass band immediately inside it is always
deployable; the base inside the INNER red boundary is not.

A CoC base is a diamond on screen, so the route is built as a diamond too:

  1. Simplify the outer red boundary to a convex isometric outline.
  2. Estimate the inner red boundary by insetting the outer outline by the
     configured corridor width (scaled from a 1350px capture).
  3. Put the route halfway between those two boundaries.
  4. Sample ``points_per_side`` points along each of the four straight
     edges, so the drops spread over all four sides.
  5. Verify every point is inside the outer boundary, outside the inner
     boundary, and clear of both red lines.

Straight edges are the point: an earlier version cast rays at evenly
spaced angles, which produced a route whose chords cut the corners of the
base — on a real capture 3 of 15 legs ran through the no-deploy area,
where a drag deploys nothing. A diamond edge always stays outside a
diamond base, so the corners survive.

Because invalid points are skipped rather than aborting the whole plan, a
base that only has room on two sides still produces a usable route.
"""

from __future__ import annotations

import math
import random
from typing import List, Sequence, Tuple

import cv2
import numpy as np

from vision.skills.red_zone_polygon import RedZonePolygonSkill

Point = Tuple[int, int]


class RingSweepPlannerSkill:
    name = "ring_sweep_planner"

    def __init__(self) -> None:
        self._inner_polygon: np.ndarray | None = None
        self._corridor_polygon: np.ndarray | None = None

    @property
    def inner_polygon(self) -> np.ndarray | None:
        return self._inner_polygon

    @property
    def corridor_polygon(self) -> np.ndarray | None:
        """The offset ring the drops sit on (YOLO mode only), else None."""
        return self._corridor_polygon

    def plan(
        self,
        polygon: np.ndarray | None,
        screen_w: int,
        ui_cutoff: int,
        config: dict | None = None,
    ) -> List[Point]:
        """Return a closed ring of deployable points around the base.

        Points walk the four edges in order, so consecutive entries are
        neighbours on the ring. Ring Sweep itself holds one point per side
        rather than walking them all, but ``deployable_arcs`` still needs
        that ordering.
        """
        self._inner_polygon = None
        self._corridor_polygon = None
        if polygon is None or len(polygon) < 3:
            return []

        cfg = (config or {}).get("ring_sweep", {})
        points_per_side = max(1, int(cfg.get("points_per_side", 4)))
        corridor_width = max(
            12,
            int(round(float(cfg.get("corridor_width_px", 40)) * screen_w / 1350.0)),
        )
        boundary_margin = max(
            2,
            int(round(float(cfg.get("boundary_margin_px", 5)) * screen_w / 1350.0)),
        )
        edge_margin = max(0, int(cfg.get("edge_margin_px", 60)))
        miter = max(1.0, float(cfg.get("corner_miter", 1.5)))

        polygon_cfg = (config or {}).get("polygon", {})
        top_limit = max(
            int(polygon_cfg.get("top_ui_exclude_px", 150)),
            edge_margin,
        )
        bottom_limit = max(top_limit + 1, ui_cutoff - edge_margin)

        centre = RedZonePolygonSkill.centroid(polygon)
        if centre is None:
            return []

        inner = self._offset_ring(polygon, centre, -corridor_width, miter)
        corners = self._offset_ring(
            polygon, centre, -corridor_width / 2.0, miter,
        )
        if inner is None or corners is None:
            return []
        self._inner_polygon = np.asarray(inner, dtype=np.int32)

        # Space the drops by DISTANCE around the lap, not by vertex. The
        # outline has uneven edges, so a fixed count per edge would bunch
        # troops onto the short ones and leave the long sides thin.
        spans = [
            math.hypot(nxt[0] - cur[0], nxt[1] - cur[1])
            for cur, nxt in zip(corners, corners[1:] + corners[:1])
        ]
        lap = sum(spans)
        total = max(1, points_per_side * 4)
        if lap <= 0:
            return []
        step = lap / total

        ring: List[Point] = []
        walked = 0.0
        for index, span in enumerate(spans):
            cur = corners[index]
            nxt = corners[(index + 1) % len(corners)]
            while walked < span and len(ring) < total:
                t = walked / span if span > 0 else 0.0
                px = int(round(cur[0] + (nxt[0] - cur[0]) * t))
                py = int(round(cur[1] + (nxt[1] - cur[1]) * t))

                # Pull points that overshoot the playfield back to the limit
                # instead of dropping them — otherwise a base sitting low or
                # wide loses that whole side and the sweep stops being "even".
                px = max(edge_margin, min(px, screen_w - edge_margin))
                py = max(top_limit, min(py, bottom_limit))

                outer_distance = cv2.pointPolygonTest(
                    polygon, (float(px), float(py)), True,
                )
                inner_distance = cv2.pointPolygonTest(
                    self._inner_polygon, (float(px), float(py)), True,
                )
                if (outer_distance >= boundary_margin
                        and inner_distance <= -boundary_margin):
                    ring.append((px, py))
                walked += step
            walked -= span

        return ring

    def plan_from_base(
        self,
        base_polygon: np.ndarray | None,
        screen_w: int,
        ui_cutoff: int,
        config: dict | None = None,
    ) -> List[Point]:
        """Deploy points on a ring a fixed offset OUTSIDE the YOLO base hull.

        ``plan`` treats its polygon as the OUTER red line and insets to find
        the grass corridor. This is the mirror image: the YOLO model returns
        the base *cluster* (the no-deploy shape), so here we take that as the
        inner boundary and step OUTWARD by ``yolo_deploy_offset_px`` — a line
        hugging the base edges — then space the drops along it.

        The drops sit right on that offset ring, so troops land next to the
        base and walk straight in. Widen the offset in config if that is too
        close for a given army.
        """
        self._inner_polygon = None
        self._corridor_polygon = None
        if base_polygon is None or len(base_polygon) < 3:
            return []

        cfg = (config or {}).get("ring_sweep", {})
        points_per_side = max(1, int(cfg.get("points_per_side", 4)))
        offset_px = max(
            4,
            int(round(float(cfg.get("yolo_deploy_offset_px", 15))
                      * screen_w / 1350.0)),
        )
        edge_margin = max(0, int(cfg.get("edge_margin_px", 60)))
        miter = max(1.0, float(cfg.get("corner_miter", 1.5)))
        margin = max(
            2,
            int(round(float(cfg.get("boundary_margin_px", 5))
                      * screen_w / 1350.0)),
        )

        polygon_cfg = (config or {}).get("polygon", {})
        top_limit = max(int(polygon_cfg.get("top_ui_exclude_px", 150)), edge_margin)
        bottom_limit = max(top_limit + 1, ui_cutoff - edge_margin)

        base = np.asarray(base_polygon, dtype=np.int32).reshape(-1, 2)
        if len(base) < 3:
            return []
        self._inner_polygon = base
        centre = RedZonePolygonSkill.centroid(base)
        if centre is None:
            return []

        # Positive distance = outward. Reuse the same mitred offset used for
        # the HSV corridor so sharp base corners get bevelled, not spiked off
        # into the trees.
        corridor = self._offset_ring(base, centre, float(offset_px), miter)
        if corridor is None or len(corridor) < 3:
            return []
        self._corridor_polygon = np.asarray(corridor, dtype=np.int32)

        # Space drops by DISTANCE around the offset lap, not by vertex, so a
        # long side is not starved by a short one.
        spans = [
            math.hypot(nxt[0] - cur[0], nxt[1] - cur[1])
            for cur, nxt in zip(corridor, corridor[1:] + corridor[:1])
        ]
        lap = sum(spans)
        total = max(1, points_per_side * 4)
        if lap <= 0:
            return []
        step = lap / total

        ring: List[Point] = []
        walked = 0.0
        for index, span in enumerate(spans):
            cur = corridor[index]
            nxt = corridor[(index + 1) % len(corridor)]
            while walked < span and len(ring) < total:
                t = walked / span if span > 0 else 0.0
                px = int(round(cur[0] + (nxt[0] - cur[0]) * t))
                py = int(round(cur[1] + (nxt[1] - cur[1]) * t))
                px = max(edge_margin, min(px, screen_w - edge_margin))
                py = max(top_limit, min(py, bottom_limit))
                # A clamped point can be dragged back onto the base — keep
                # only points that still sit clearly OUTSIDE it.
                if cv2.pointPolygonTest(
                    base, (float(px), float(py)), True,
                ) <= -margin:
                    ring.append((px, py))
                walked += step
            walked -= span

        return ring

    # ── Helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _outline(polygon: np.ndarray) -> np.ndarray | None:
        """The base's convex outline, denoised down to a few corners.

        The convex hull — not a forced quad. A quad circumscribing the
        17-gon the detector returns has to bulge far past the base to
        contain it: measured on a real capture, its side corner landed at
        x=-41 on a 1350px screen. The hull already reads as a diamond for
        a CoC base, and insetting that convex shape gives a stable inner
        corridor boundary without following small gaps in the red pixels.
        """
        verts = polygon.reshape(-1, 2).astype(np.int32)
        if len(verts) < 3:
            return None
        hull = cv2.convexHull(verts)
        simple = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
        if len(simple) < 3:
            simple = hull
        return simple.reshape(-1, 2)

    @staticmethod
    def _offset_ring(
        polygon: np.ndarray, centre: Point, distance: float,
        miter: float = 1.5,
    ) -> List[Point] | None:
        """Shift the outer outline by signed ``distance`` pixels.

        Positive moves outward; negative moves inward. Corners stay in hull
        order, so consecutive entries remain neighbours on the corridor.
        """
        outline = RingSweepPlannerSkill._outline(polygon)
        if outline is None:
            return None

        cx, cy = centre
        rel = [(float(x) - cx, float(y) - cy) for x, y in outline]

        # Outward normal and distance-from-centre of every edge.
        edges = []
        for index, (ax, ay) in enumerate(rel):
            bx, by = rel[(index + 1) % len(rel)]
            ex, ey = bx - ax, by - ay
            length = math.hypot(ex, ey)
            if length <= 1.0:
                continue                                # duplicate vertex
            nx, ny = ey / length, -ex / length          # right-hand normal
            offset = nx * ax + ny * ay
            if offset < 0:                              # winding was reversed
                nx, ny, offset = -nx, -ny, -offset
            if offset <= 1.0:
                return None                             # centre not enclosed
            edges.append((nx, ny, offset))
        if len(edges) < 3:
            return None

        # approxPolyDP simplifies by dropping vertices, so a simplified edge
        # can slice INSIDE the hull and leave part of the base sticking out
        # past it. Pull each edge out to the furthest point it has to clear
        # first, otherwise the corridor width is measured from the wrong line
        # and taps can land too close to either red boundary.
        rel_all = [(float(vx) - cx, float(vy) - cy)
                   for vx, vy in polygon.reshape(-1, 2)]
        edges = [
            (nx, ny, max([nx * rx + ny * ry for rx, ry in rel_all] + [offset]))
            for nx, ny, offset in edges
        ]

        def meet(first, second) -> tuple[float, float] | None:
            """Where two edges cross, relative to the centroid."""
            n1x, n1y, o1 = first
            n2x, n2y, o2 = second
            det = n1x * n2y - n1y * n2x
            if abs(det) < 1e-6:
                return None                             # parallel edges
            return ((o1 * n2y - o2 * n1y) / det,
                    (o2 * n1x - o1 * n2x) / det)

        # Each corner is where two consecutive offset edges meet. At a sharp
        # OUTWARD corner that intersection runs away — the sharper the angle,
        # the further — and on a real base it landed off-screen in the trees.
        #
        # Past the miter limit, cut the corner off instead: step out from the
        # base corner along EACH adjacent edge's normal. Simply shortening
        # the spike would drag the route back towards the base — that left
        # drops only 5px from the no-deploy zone, close enough to be swallowed
        # by detection error — whereas a bevel keeps the full clearance.
        limit = abs(distance) * max(1.0, miter)
        pushed = [(nx, ny, offset + distance) for nx, ny, offset in edges]
        corners: List[Point] = []
        for index in range(len(pushed)):
            tip = meet(pushed[index - 1], pushed[index])
            base = meet(edges[index - 1], edges[index])
            if tip is None or base is None:
                return None
            # An inward intersection converges towards the centre, so it must
            # remain one corner. Beveling it creates two reversed points and
            # a self-crossing X at sharp left/right tips of a real base.
            if (distance <= 0
                    or math.hypot(tip[0] - base[0], tip[1] - base[1]) <= limit):
                corners.append((int(round(cx + tip[0])),
                                int(round(cy + tip[1]))))
                continue
            for nx, ny, _offset in (pushed[index - 1], pushed[index]):
                corners.append((int(round(cx + base[0] + nx * distance)),
                                int(round(cy + base[1] + ny * distance))))
        return corners

    @staticmethod
    def side_of(centre: Point, point: Point) -> str:
        """Which side of the base a ring point sits on."""
        dx = point[0] - centre[0]
        dy = point[1] - centre[1]
        if abs(dx) >= abs(dy):
            return "right" if dx >= 0 else "left"
        return "bottom" if dy >= 0 else "top"

    @staticmethod
    def sides_covered(centre: Point, ring: Sequence[Point]) -> set[str]:
        return {RingSweepPlannerSkill.side_of(centre, p) for p in ring}

    def deployable_arcs(
        self,
        polygon: np.ndarray,
        route: Sequence[Point],
        samples: int = 12,
    ) -> List[List[Point]]:
        """Split the route into runs whose legs stay out of the no-deploy zone.

        Ring points are verified individually, but the straight leg between
        two of them is not: wherever a point was rejected the route has a
        gap, and the shortcut across it can cut through the base. Dragging
        there wastes the press — the game drops nothing while the finger is
        inside the red zone — so the route is broken at those legs and each
        surviving run is dragged as one continuous press.

        Runs, not individual legs: every press pays a ~1 s ramp before the
        game starts deploying, so re-pressing per leg would spend most of
        the attack waiting.
        """
        if len(route) < 2:
            return []

        def blocked(start: Point, end: Point) -> bool:
            for step in range(1, samples):
                t = step / float(samples)
                mx = int(round(start[0] + (end[0] - start[0]) * t))
                my = int(round(start[1] + (end[1] - start[1]) * t))
                if not RedZonePolygonSkill.is_inside(polygon, mx, my):
                    return True
                if (self._inner_polygon is not None
                        and RedZonePolygonSkill.is_inside(
                            self._inner_polygon, mx, my,
                        )):
                    return True
            return False

        arcs: List[List[Point]] = []
        current: List[Point] = [route[0]]
        for index in range(len(route)):
            start, end = route[index], route[(index + 1) % len(route)]
            if blocked(start, end):
                if len(current) > 1:
                    arcs.append(current)
                current = [end]
            else:
                current.append(end)
        if len(current) > 1:
            arcs.append(current)

        # A fully clear route closes on itself: the trailing run and the
        # leading one are the same lap, so joining them avoids lifting the
        # finger in the middle of an otherwise continuous circuit.
        if len(arcs) > 1 and arcs[0][0] == arcs[-1][-1]:
            arcs[0] = arcs[-1] + arcs[0][1:]
            arcs.pop()
        return arcs

    @staticmethod
    def pick_drops(
        centre: Point,
        ring: Sequence[Point],
        count: int | None = None,
        rng=random,
    ) -> List[Point]:
        """Choose ``count`` drop points spread as evenly as the ring allows.

        Sides are visited round-robin in random order, so the first four
        points of a four-sided base are one per side — asking for more
        comes back round and takes a second point from each side, asking
        for fewer simply leaves the tail sides out. ``None`` means "one
        per side", the historical behaviour.

        Both the side order and the point within a side are random on
        purpose: the same base attacked twice should not produce the same
        drop spots in the same order. Points are never repeated, so a
        request for more points than the ring holds returns the whole ring
        rather than the same spot twice — two fingers on one pixel is one
        finger as far as the game is concerned.
        """
        by_side: dict[str, List[Point]] = {}
        for point in ring:
            by_side.setdefault(
                RingSweepPlannerSkill.side_of(centre, point), [],
            ).append(point)
        sides = sorted(by_side)
        if not sides:
            return []
        rng.shuffle(sides)

        pools: dict[str, List[Point]] = {}
        for side, points in by_side.items():
            pool = list(points)
            rng.shuffle(pool)
            pools[side] = pool

        wanted = len(sides) if count is None else max(1, int(count))
        drops: List[Point] = []
        index = 0
        while len(drops) < wanted:
            if not any(pools.values()):
                break                       # the ring is exhausted
            pool = pools[sides[index % len(sides)]]
            index += 1
            if pool:
                drops.append(pool.pop())
        return drops

    @staticmethod
    def one_point_per_side(
        centre: Point, ring: Sequence[Point], rng=random,
    ) -> List[Point]:
        """One random ring point from each side, in random side order.

        Sides with no surviving ring point are simply absent, so a base
        that only has grass on two sides still returns two points.
        """
        return RingSweepPlannerSkill.pick_drops(centre, ring, None, rng)

    @staticmethod
    def randomize_route(points: Sequence[Point], rng=random) -> List[Point]:
        """Rotate the closed ring to a random start and random direction."""
        if not points:
            return []
        start = rng.randrange(len(points))
        direction = rng.choice((1, -1))
        return [
            points[(start + direction * i) % len(points)]
            for i in range(len(points))
        ]
