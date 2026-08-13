"""Plan an evenly-spaced deploy ring that hugs the base on all four sides.

Why this exists alongside ``PerimeterPlannerSkill``
---------------------------------------------------
``PerimeterPlannerSkill`` walks the centre lines of the four *screen-edge*
corridors, and it bails out unless **all four** corridors exist. In practice
the corridor mapper only reports a side when there is enough empty screen
between the red-zone bbox and the screen border, so on most bases it returns
just ``{'left'}`` or ``{'left','right'}`` and the sweep never runs.

This planner works off the red-zone polygon instead of screen geometry.

A CoC base is a diamond on screen, so the route is built as a diamond too:

  1. Simplify the red-zone hull down to four corners — for an isometric
     base those are the corners of its diamond.
  2. Push each of the four EDGES outward until it clears the whole polygon,
     then a further ``clearance_px``. That lands the route in the grass
     band between the no-deploy area and the trees.
  3. Sample ``points_per_side`` points along each of the four straight
     edges, so the drops spread over all four sides.
  4. Verify each point is outside the polygon and inside the playfield,
     and drop it if not.

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
        if polygon is None or len(polygon) < 3:
            return []

        cfg = (config or {}).get("ring_sweep", {})
        points_per_side = max(1, int(cfg.get("points_per_side", 4)))
        clearance = max(0, int(cfg.get("clearance_px", 45)))
        edge_margin = max(0, int(cfg.get("edge_margin_px", 60)))
        miter = max(1.0, float(cfg.get("corner_miter", 1.5)))
        # A drop must keep at least this much grass under it to
        # survive detection error; below that the point is useless.
        min_gap = max(1, int(clearance * float(cfg.get("min_gap_ratio", 0.5))))

        polygon_cfg = (config or {}).get("polygon", {})
        top_limit = max(
            int(polygon_cfg.get("top_ui_exclude_px", 150)),
            edge_margin,
        )
        bottom_limit = max(top_limit + 1, ui_cutoff - edge_margin)

        centre = RedZonePolygonSkill.centroid(polygon)
        if centre is None:
            return []

        corners = self._offset_ring(polygon, centre, clearance, miter)
        if corners is None:
            return []

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

                # Must be genuinely clear of the no-deploy zone. Clamping
                # drags a point back towards the base, and on a wide base
                # that left drops 4px from the edge — inside it once
                # detection error is counted, so the troop never lands.
                # Anything that ends up hugging the boundary is dropped.
                if not RedZonePolygonSkill.is_inside(polygon, px, py, min_gap):
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
        a CoC base, and offsetting a convex shape keeps every leg outside
        it, so the corners stay where the grass is.
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
        polygon: np.ndarray, centre: Point, clearance: int,
        miter: float = 1.5,
    ) -> List[Point] | None:
        """The base outline pushed ``clearance`` px outward into the grass.

        Corners come back in hull order, so consecutive entries are
        neighbours and the edges between them enclose the base.
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
        # first, otherwise the clearance is measured from the wrong line and
        # drops land a few px from the no-deploy zone.
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
        # corner that intersection runs away — the sharper the angle, the
        # further — and on a real base it landed off-screen in the trees.
        #
        # Past the miter limit, cut the corner off instead: step out from the
        # base corner along EACH adjacent edge's normal. Simply shortening
        # the spike would drag the route back towards the base — that left
        # drops only 5px from the no-deploy zone, close enough to be swallowed
        # by detection error — whereas a bevel keeps the full clearance.
        limit = clearance * max(1.0, miter)
        pushed = [(nx, ny, offset + clearance) for nx, ny, offset in edges]
        corners: List[Point] = []
        for index in range(len(pushed)):
            tip = meet(pushed[index - 1], pushed[index])
            base = meet(edges[index - 1], edges[index])
            if tip is None or base is None:
                return None
            if math.hypot(tip[0] - base[0], tip[1] - base[1]) <= limit:
                corners.append((int(round(cx + tip[0])),
                                int(round(cy + tip[1]))))
                continue
            for nx, ny, _offset in (pushed[index - 1], pushed[index]):
                corners.append((int(round(cx + base[0] + nx * clearance)),
                                int(round(cy + base[1] + ny * clearance))))
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

    @staticmethod
    def deployable_arcs(
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
                if RedZonePolygonSkill.is_inside(polygon, mx, my):
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
