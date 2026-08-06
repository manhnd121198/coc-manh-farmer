"""Stop the mouse wheel from silently changing settings.

Qt's default is that a combo box, spin box or slider under the cursor
takes the wheel even when it does not have focus. Scrolling down a long
settings page therefore rewrites whatever value the pointer happens to
pass over — a threshold, a preset, a troop count — with no click and no
visible sign that anything changed.

That is how vision_ui_threshold ended up at 0.4 during a session: nobody
edited it on purpose.

With this filter installed the wheel only reaches such a widget once it
has been CLICKED. Otherwise the event is left alone and the scroll area
receives it, which is what the wheel is normally for.
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject
from PyQt5.QtWidgets import QAbstractSpinBox, QComboBox, QDial, QSlider

# Widgets whose value the wheel would otherwise edit in passing.
#
# Listed one by one rather than via QAbstractSlider, which would also
# catch QScrollBar — and a scroll bar that ignores the wheel is exactly
# the thing this is trying to protect.
GUARDED = (QAbstractSpinBox, QComboBox, QSlider, QDial)


class WheelGuard(QObject):
    """Application-wide filter: wheel edits only a focused control."""

    def eventFilter(self, obj, event):  # noqa: N802  (Qt naming)
        if event.type() != QEvent.Wheel or not isinstance(obj, GUARDED):
            return False
        if obj.hasFocus():
            return False                     # clicked into it — let it edit
        # Refuse the event so it bubbles to the scroll area behind. Qt only
        # forwards it to the parent when the handler reports "not accepted".
        event.ignore()
        return True


_guard: WheelGuard | None = None


def install(app) -> WheelGuard:
    """Arm the guard for every widget, including ones built later.

    Filtering at the application level means tabs and dialogs created
    after start-up are covered too, so a control added somewhere that
    forgot to opt in cannot reintroduce the problem.
    """
    global _guard
    if _guard is None:
        _guard = WheelGuard()
    app.installEventFilter(_guard)
    return _guard
