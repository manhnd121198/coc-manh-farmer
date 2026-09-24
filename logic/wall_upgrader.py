"""Spend surplus gold or elixir on walls, through the builder menu.

The route, measured on a live TH13 at 1350x1080:

    builder icon -> panel opens
    scroll to the bottom -> "Tường thành xN" is near the end
    tap that row -> camera jumps to a wall, one segment selected
    "Nâng cấp thêm" -> the bar turns into a counter (-1 / +10 / +1)
    "+1" until the wanted count -> price = count x per-segment cost
    "Nâng cấp" (gold or elixir) -> a confirm dialog naming BOTH the
    amount and the currency -> Ok

Why this file does not tap fixed coordinates
--------------------------------------------
The action bar is centre-aligned, so its buttons MOVE when their number
changes: after an upgrade the row lost "Nâng cấp thêm" and every
remaining button slid ~72px right. The coordinate that pressed "upgrade
with gold" before then pointed between two buttons.

Why it does not identify buttons by template either
---------------------------------------------------
The gold and elixir upgrade buttons are the same button with a different
currency icon. Measured cross-match of a whole-button template: 1.000 on
the right button and **0.940 on the wrong one**. A template here would
feel safe and quietly spend the wrong resource. Cropping just the icon
only reaches a 0.26-0.40 margin, because a coin and a drop grey out to
nearly the same thing.

So currency is decided by HUE, which is unambiguous: the coin reads 25
and the drop 148, identically across every frame measured, on both the
4-button and 5-button layouts.

The "+1" and "+10" buttons cannot be told apart at all — 0.882
cross-match, hues 90 vs 101 — so nothing here relies on knowing which is
which. The count is re-read from the title after every press instead, and
the run aborts if a press did not do what was expected. The confirm
dialog, which spells out the amount and the resource, is the last gate.
"""

from __future__ import annotations

import re
import time
import unicodedata

import cv2
import numpy as np

from core.adb_handler import _run as _adb_run, screencap, tap
from core.logger import BotLogger
from core.settings import Settings

log = BotLogger.get("wall")

# Builder-hut counter in the top HUD, as a fraction of the screen.
BUILDER_ICON = (0.504, 0.042)

# The scrollable list inside the builder panel.
PANEL = (0.35, 0.60, 0.35, 0.685)          # y1, y2, x1, x2 (fractions)
PANEL_BOX = (0.102, 0.602, 0.350, 0.685)   # y1, y2, x1, x2 of the whole panel
SCROLL_FROM = (0.519, 0.556)
SCROLL_TO = (0.519, 0.352)
MAX_SCROLLS = 25

# Where the action bar and its title live.
BAR_BAND = (0.74, 0.90)
TITLE_BAND = (0.705, 0.762)

# Currency icon hues (OpenCV 0..179).
HUE_GOLD, HUE_ELIXIR, HUE_TOLERANCE = 25, 148, 12

CONFIRM_OK = (0.621, 0.613)
CONFIRM_CANCEL = (0.379, 0.613)

# How many rows the OCR must have read before "no walls in this list" is
# believed. A real builder list is long — the live one read 23 rows — so
# anything this short means the read failed, not that the base is done.
MIN_LEGIBLE_ROWS = 5

C_GREEN, C_RESET = "\033[92m", "\033[0m"


def _strip(text: str) -> str:
    """Lowercase and drop diacritics — the OCR runs an English model."""
    plain = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in plain if unicodedata.category(c) != "Mn")


def is_wall_text(text: str) -> bool:
    """Whether an OCR line names the walls, allowing for how it misreads.

    Nothing longer survives the English OCR model. The same three words
    came back as "Tuong THaNH", "3 Tong tHaNH" and "Tuong +HanH" — so
    "tuong thanh" never matches, and even "tuong" is missing from one of
    them. What holds in all three is "ong" and "anh".

    Neither fragment can stand alone: "Tháp săn Tướng" sits in the very
    same list and has the "ong", while "Doanh trại Quân đội" has the
    "anh". Both are rejected by requiring the pair. "cap" is accepted in
    place of "ong" because the selection title always carries the level,
    as in "(Cấp 13)".

    This is deliberately loose, so it is not the last word: the confirm
    dialog is checked with this same test before anything is spent.
    """
    squashed = re.sub(r"[^a-z0-9]", "", _strip(text))
    return "anh" in squashed and ("ong" in squashed or "cap" in squashed)


class WallUpgrader:
    def __init__(self, screen_reader, ocr) -> None:
        self._sr = screen_reader
        self._ocr = ocr

    # ── Screen reading ──────────────────────────────────────────────

    @staticmethod
    def find_action_buttons(ss: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Every button box in the bottom action bar, left to right.

        Found by shape and shade rather than counted from a fixed layout,
        because the bar holds four or five buttons depending on what the
        selected wall can still do.
        """
        h, w = ss.shape[:2]
        y0, y1 = int(h * BAR_BAND[0]), int(h * BAR_BAND[1])
        band = ss[y0:y1, :]
        if band.size == 0:
            return []
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        pale = ((hsv[:, :, 1] <= 70) & (hsv[:, :, 2] >= 165))
        mask = (pale.astype(np.uint8) * 255)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )
        out = []
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            if 90 <= cw <= 190 and 90 <= ch <= 175:
                out.append((x + cw // 2, y0 + y + ch // 2, cw, ch))
        return sorted(out)

    @staticmethod
    def currency_of(ss: np.ndarray, button: tuple[int, int, int, int]) -> str | None:
        """'gold', 'elixir' or None, from the icon in the button's corner."""
        cx, cy, bw, bh = button
        left, top = cx - bw // 2, cy - bh // 2
        patch = ss[top + int(bh * 0.05):top + int(bh * 0.28),
                   left + int(bw * 0.78):left + bw]
        if patch.size == 0:
            return None
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        strong = (hsv[:, :, 1] > 90) & (hsv[:, :, 2] > 90)
        if int(strong.sum()) < 20:
            return None
        hue = float(np.median(hsv[:, :, 0][strong]))
        if abs(hue - HUE_GOLD) <= HUE_TOLERANCE:
            return "gold"
        if abs(hue - HUE_ELIXIR) <= HUE_TOLERANCE:
            return "elixir"
        return None

    @staticmethod
    def price_is_red(ss: np.ndarray, button: tuple[int, int, int, int]) -> bool:
        """True when the button's price is printed red — cannot afford it.

        The game turns the figure red the moment the cost passes what you
        hold; verified live at 3 000 000 against 2 929 845 gold.
        """
        cx, cy, bw, bh = button
        left, top = cx - bw // 2, cy - bh // 2
        patch = ss[top:top + int(bh * 0.30), left:left + bw]
        if patch.size == 0:
            return False
        hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        strong = (sat > 110) & (val > 110)
        red = strong & ((hue <= 10) | (hue >= 170))
        return int(red.sum()) >= 60

    def upgrade_buttons(
        self, ss: np.ndarray,
    ) -> dict[str, tuple[int, int, int, int]]:
        """The two 'Nâng cấp' buttons, keyed by what they spend."""
        found: dict[str, tuple[int, int, int, int]] = {}
        for button in self.find_action_buttons(ss):
            which = self.currency_of(ss, button)
            if which and which not in found:
                found[which] = button
        return found

    def selected_count(self, ss: np.ndarray) -> int | None:
        """How many segments the title says are selected.

        The title reads "3 Tường thành (Cấp 13)" while counting and just
        "Tường thành (Cấp 13)" before the counter is armed — the latter
        means one segment.
        """
        h, w = ss.shape[:2]
        crop = ss[int(h * TITLE_BAND[0]):int(h * TITLE_BAND[1]),
                  int(w * 0.25):int(w * 0.80)]
        if crop.size == 0:
            return None
        text = self._ocr_text(crop)
        if not is_wall_text(text):
            return None
        lead = re.match(r"\s*(\d+)", _strip(text))
        return int(lead.group(1)) if lead else 1

    def confirm_text(self, ss: np.ndarray) -> str:
        h, w = ss.shape[:2]
        crop = ss[int(h * 0.30):int(h * 0.55), int(w * 0.23):int(w * 0.78)]
        return _strip(self._ocr_text(crop)) if crop.size else ""

    def _ocr_text(self, crop: np.ndarray) -> str:
        from vision.ocr_reader import _get_reader
        reader = _get_reader()
        if reader is None:
            return ""
        try:
            up = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            return " ".join(reader.readtext(up, detail=0, paragraph=True))
        except Exception as exc:
            log.error("Wall OCR failed: %s", exc)
            return ""

    # ── Acting ──────────────────────────────────────────────────────

    @staticmethod
    def _tap_fraction(ss: np.ndarray, point: tuple[float, float]) -> None:
        h, w = ss.shape[:2]
        tap(int(w * point[0]), int(h * point[1]))

    @staticmethod
    def _scroll(ss: np.ndarray) -> None:
        h, w = ss.shape[:2]
        _adb_run(["shell", "input", "swipe",
                  str(int(w * SCROLL_FROM[0])), str(int(h * SCROLL_FROM[1])),
                  str(int(w * SCROLL_TO[0])), str(int(h * SCROLL_TO[1])), "400"])

    @staticmethod
    def _panel_patch(ss: np.ndarray) -> np.ndarray:
        h, w = ss.shape[:2]
        return ss[int(h * PANEL_BOX[0]):int(h * PANEL_BOX[1]),
                  int(w * PANEL_BOX[2]):int(w * PANEL_BOX[3])]

    def _panel_settled(self, before: np.ndarray, after: np.ndarray) -> bool:
        """Whether a swipe moved the list at all — i.e. we hit the end."""
        if before.shape != after.shape:
            return False
        moved = float(np.mean(np.abs(
            before.astype(np.int16) - after.astype(np.int16))))
        return moved < 2.0

    def search_for_wall_row(self) -> tuple[tuple[int, int] | None, int]:
        """Scan the WHOLE builder list for the wall row, screen by screen.

        Every screenful is read on the way down instead of only the last
        one. The list is ordered by price and a wall segment gets dearer
        with each level, so the row drifts through the list over time —
        there is no position it can be relied on to hold. It merely
        happened to sit second from last the day this was written.

        Reading only the final screen would not just miss it: the caller
        treats "no wall row" as "the walls are finished" and switches the
        whole feature off, so a row scrolled past would silently retire a
        feature that still had work to do.

        Returns ``(point_or_None, lines_read)`` with the lines totalled
        across every screen, so the caller can still tell a finished base
        from an unreadable one.
        """
        shot = screencap()
        seen = 0
        for _ in range(MAX_SCROLLS):
            if shot is None:
                return None, seen
            row, lines = self.find_wall_row(shot)
            seen += lines
            if row is not None:
                return row, seen

            before = self._panel_patch(shot)
            self._scroll(shot)
            time.sleep(0.9)
            shot = screencap()
            if shot is None:
                return None, seen
            if self._panel_settled(before, self._panel_patch(shot)):
                # The list did not move, so this screen is the one just
                # read at the top of the loop. Reading it again would cost
                # another OCR pass for nothing and double-count its lines.
                return None, seen

        log.warning("Danh sách nâng cấp không dừng cuộn sau %d lần.", MAX_SCROLLS)
        return None, seen

    def find_wall_row(
        self, ss: np.ndarray,
    ) -> tuple[tuple[int, int] | None, int]:
        """Locate the "Tường thành xN" row, and say how much was readable.

        Returns ``(point_or_None, lines_read)``. The second value is what
        separates "the walls are finished" from "the OCR had a bad
        frame" — both of which return None for the point, and only one of
        which should switch the feature off.
        """
        from vision.ocr_reader import _get_reader
        reader = _get_reader()
        if reader is None:
            return None, 0
        h, w = ss.shape[:2]
        y0, x0 = int(h * PANEL_BOX[0]), int(w * PANEL_BOX[2])
        crop = ss[y0:int(h * PANEL_BOX[1]), x0:int(w * PANEL_BOX[3])]
        if crop.size == 0:
            return None, 0
        try:
            hits = reader.readtext(crop, detail=1, paragraph=False)
        except Exception as exc:
            log.error("Wall row OCR failed: %s", exc)
            return None, 0
        for box, text, _conf in hits:
            if is_wall_text(text):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                return (x0 + int(sum(xs) / len(xs)),
                        y0 + int(sum(ys) / len(ys))), len(hits)
        return None, len(hits)

    # ── The run ─────────────────────────────────────────────────────

    def run(self, segments: int = 5, currency: str = "gold") -> bool:
        """Upgrade ``segments`` walls, paying with ``currency``.

        Returns True only when the confirm dialog was accepted. Every
        other outcome — list not found, count would not grow, price red,
        confirm text naming the wrong resource — backs out without
        spending, because a half-done wall run costs real resources and
        nothing here is worth guessing at.
        """
        if currency not in ("gold", "elixir"):
            log.error("Loại tài nguyên không hợp lệ: %r", currency)
            return False

        shot = screencap()
        if shot is None:
            return False

        self._tap_fraction(shot, BUILDER_ICON)
        time.sleep(1.5)

        row, lines = self.search_for_wall_row()
        if row is None:
            self._handle_missing_row(lines)
            self._close()
            return False

        tap(row[0], row[1])
        time.sleep(2.0)

        if not self._grow_selection(segments):
            self._close()
            return False

        shot = screencap()
        if shot is None:
            return False
        buttons = self.upgrade_buttons(shot)
        button = buttons.get(currency)
        if button is None:
            log.warning(
                "Không tìm thấy nút nâng cấp bằng %s (thấy: %s).",
                currency, ", ".join(sorted(buttons)) or "không có nút nào",
            )
            self._close()
            return False
        if self.price_is_red(shot, button):
            log.info("Giá đang màu đỏ — không đủ %s, bỏ qua.", currency)
            self._close()
            return False

        tap(button[0], button[1])
        time.sleep(2.0)
        return self._confirm(currency)

    def _handle_missing_row(self, lines: int) -> None:
        """No wall row in the list. Decide whether that means "finished".

        Only called after the ENTIRE list has been scanned, not just its
        last screen — see search_for_wall_row for why that distinction
        decides whether this is a fact or a guess.

        Every wall being maxed is the one condition under which this
        feature should stop asking, so it switches itself off — otherwise
        every trip home would pay a builder-panel scroll and a full OCR
        pass forever, for a row that will never come back.

        But a blank read is NOT a finished base. The English OCR model is
        rough on this text: it has already turned "Tường" into "Tong",
        dropping a letter outright. One unlucky frame must not disable a
        feature the user turned on, so the list has to have been legible —
        several rows read — before the absence of walls counts as proof.
        """
        if lines < MIN_LEGIBLE_ROWS:
            log.warning(
                "Không đọc được danh sách nâng cấp (chỉ %d dòng) — chưa "
                "kết luận được, để nguyên cấu hình.", lines,
            )
            return

        settings = Settings()
        settings.set("wall_upgrade_enabled", False)
        settings.save()
        log.info(
            "%s🧱 Đọc được %d dòng mà không có tường thành — tường đã nâng "
            "tối đa. Đã tự tắt 'Tự nâng tường'.%s", C_GREEN, lines, C_RESET,
        )

    def _grow_selection(self, want: int) -> bool:
        """Press the add button until the title says ``want`` are selected.

        The count is re-read from the title after every press instead of
        being counted, because "+1" and "+10" cannot be told apart — they
        cross-match at 0.882 and their hues are 90 against 101. A press
        that does not move the count by one is treated as a failure rather
        than retried, since the alternative is silently selecting ten.
        """
        for _ in range(max(0, want)):
            shot = screencap()
            if shot is None:
                return False
            count = self.selected_count(shot)
            if count is None:
                log.warning("Không đọc được số đoạn đang chọn — dừng.")
                return False
            if count >= want:
                return True

            buttons = self.find_action_buttons(shot)
            add = self._add_button(shot, buttons)
            if add is None:
                log.warning("Không xác định được nút thêm đoạn — dừng.")
                return False
            tap(add[0], add[1])
            time.sleep(1.2)

            after = screencap()
            moved = self.selected_count(after) if after is not None else None
            if moved is None or moved <= count:
                log.warning(
                    "Bấm thêm đoạn không có tác dụng (%s → %s) — dừng.",
                    count, moved,
                )
                return False
            if moved > want:
                log.warning(
                    "Chọn quá tay: muốn %d đoạn, đang %d — dừng để khỏi "
                    "tiêu nhầm.", want, moved,
                )
                return False

        shot = screencap()
        return shot is not None and self.selected_count(shot) == want

    def _add_button(
        self, ss: np.ndarray, buttons: list[tuple[int, int, int, int]],
    ) -> tuple[int, int, int, int] | None:
        """The button that adds ONE segment.

        Anchored to the gold button, which is the one thing on this bar
        that can be identified outright, rather than to the edge of the
        row: "+1" always sits immediately to its left. Whether that press
        really added one is checked afterwards by re-reading the title.
        """
        gold = self.upgrade_buttons(ss).get("gold")
        if gold is None or not buttons:
            return None
        left_of_gold = [b for b in buttons if b[0] < gold[0]]
        return max(left_of_gold, key=lambda b: b[0]) if left_of_gold else None

    def _confirm(self, currency: str) -> bool:
        """Read the dialog, and only press Ok if it names the right resource.

        The amount in that sentence cannot be trusted — EasyOCR read
        6 000 000 as "80ooooo" — so the figure is not checked here. It was
        already established upstream: the title gave the segment count and
        the button's price was not red. What this gate adds is the one
        thing nothing else can prove, that the game is about to spend the
        resource we chose.
        """
        shot = screencap()
        if shot is None:
            return False
        text = self.confirm_text(shot)
        if not text:
            log.warning("Không đọc được hộp xác nhận — bấm Hủy cho chắc.")
            self._tap_fraction(shot, CONFIRM_CANCEL)
            return False

        if not is_wall_text(text):
            # The row match that got us here is loose by necessity. If the
            # game is asking about something other than walls, that is
            # where it shows, and nothing has been spent yet.
            log.error("Hộp xác nhận không nói về tường thành: %r — Hủy.", text)
            self._tap_fraction(shot, CONFIRM_CANCEL)
            return False

        wants_gold = "vang" in text
        wants_elixir = "duoc" in text or "elixir" in text
        if (currency == "gold") != wants_gold or (currency == "elixir") != wants_elixir:
            log.error(
                "Hộp xác nhận nói tài nguyên khác với yêu cầu (%s): %r — Hủy.",
                currency, text,
            )
            self._tap_fraction(shot, CONFIRM_CANCEL)
            return False

        self._tap_fraction(shot, CONFIRM_OK)
        time.sleep(2.5)
        log.info("🧱 Đã nâng tường bằng %s.", "vàng" if currency == "gold" else "elixir")
        return True

    @staticmethod
    def _close() -> None:
        """Back out to the village, whatever is open."""
        _adb_run(["shell", "input", "keyevent", "4"])
        time.sleep(1.0)
