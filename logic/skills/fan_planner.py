"""
FanPlannerSkill — produces an evenly-spaced line of drop points along
the long axis of a corridor. Used for air troops and unbuffered ground
swarms (archers, barbarians).

The fan is symmetric around the corridor center, leaves a small margin
at each end, and exposes the geometric center as the "cluster" anchor
for spells / heroes.
"""

from __future__ import annotations

from typing import List, Tuple

from core.logger import BotLogger
from vision.skills.safe_corridor import SafeCorridorSkill, Rect

log = BotLogger.get("v2.fan_planner")


class FanPlannerSkill:
    name = "fan_planner"

    def plan(
        self,
        corridor: Rect,
        count: int = 9,
        margin_pct: float = 0.10,
        side: str | None = None,
        edge_bias: float = 0.0,
        edge_margin_px: int = 60,
    ) -> List[Tuple[int, int]]:
        """Points along the corridor's long axis.

        ``edge_bias`` slides them across the SHORT axis: 0.0 keeps the
        historic corridor centre, 1.0 pushes them to the outer rim
        (``edge_margin_px`` in from the corridor's far edge). The rim is
        the part of the map that cannot hold enemy buildings, so biasing
        outwards trades a longer walk for a drop that always lands.

        ``side`` ("left" / "right" / "top" / "bottom") says which way is
        outwards; without it the bias is ignored, because guessing would
        push half the drops straight into the base.
        """
        if corridor is None:
            return []
        x, y, w, h = corridor
        if count < 1:
            return []
        if count == 1:
            return [SafeCorridorSkill.center(corridor)]

        # Orientation comes from the SIDE, not from the rectangle's
        # aspect: a left/right corridor can easily come out wider than it
        # is tall (579x546 on a 16:9 screen), and reading that as
        # "horizontal" strings the fan from the base outwards — the first
        # drops then land on the ring of trees and decorations hugging
        # the base instead of along the clear rim.
        if side in ("left", "right"):
            fan_runs_vertically = True
        elif side in ("top", "bottom"):
            fan_runs_vertically = False
        else:
            fan_runs_vertically = not SafeCorridorSkill.is_horizontal(corridor)

        if fan_runs_vertically:
            my = int(h * margin_pct)
            y_lo = y + my
            y_hi = y + h - my
            cx = self._cross_axis(
                lo=x, span=w, side=side, outward_sides=("left", "right"),
                low_is_outward=(side == "left"),
                bias=edge_bias, margin_px=edge_margin_px,
            )
            step = (y_hi - y_lo) / float(count - 1)
            return [(cx, int(round(y_lo + step * i))) for i in range(count)]
        else:
            mx = int(w * margin_pct)
            x_lo = x + mx
            x_hi = x + w - mx
            cy = self._cross_axis(
                lo=y, span=h, side=side, outward_sides=("top", "bottom"),
                low_is_outward=(side == "top"),
                bias=edge_bias, margin_px=edge_margin_px,
            )
            step = (x_hi - x_lo) / float(count - 1)
            return [(int(round(x_lo + step * i)), cy) for i in range(count)]

    @staticmethod
    def _cross_axis(
        lo: int,
        span: int,
        side: str | None,
        outward_sides: tuple[str, ...],
        low_is_outward: bool,
        bias: float,
        margin_px: int,
    ) -> int:
        """Position on the corridor's short axis, interpolated between
        its centre (bias 0) and its outer rim (bias 1)."""
        centre = lo + span / 2.0
        bias = max(0.0, min(1.0, float(bias)))
        if bias <= 0.0 or side not in outward_sides:
            return int(centre)

        margin = max(0, int(margin_px))
        if low_is_outward:
            rim = lo + margin
            rim = min(rim, centre)        # never cross the centre line
        else:
            rim = lo + span - margin
            rim = max(rim, centre)
        return int(round(centre + (rim - centre) * bias))

    def cluster_anchor(self, corridor: Rect) -> Tuple[int, int]:
        return SafeCorridorSkill.center(corridor)
