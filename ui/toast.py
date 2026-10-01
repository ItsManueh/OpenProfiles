"""
Toast notifications in the style of Sonner (the ones SpotiFLAC uses): small cards
tinted by their type, with a colored icon and lowercase monospaced text, that slide
in at the bottom-right corner of a window, stack upwards and fade away on their own.

- Hovering a toast keeps it on screen; clicking it dismisses it.
- A toast can carry one action, e.g. "Undo" after deleting a profile.
- Toasts of the same group that arrive while one is still visible are merged
  ("3 profiles opened") instead of piling up; their different details are listed.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QEnterEvent, QFontMetrics, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.icons import IconView
from ui.widgets import make_label

CARD_WIDTH = 356  # the visible card (Sonner's width)
SHADOW = 14  # room around the card for its shadow
SHADOW_OFFSET = 4  # the shadow falls a little below the card
RADIUS = 10  # corner radius of the card (see the style sheet)
WIDTH = CARD_WIDTH + 2 * SHADOW
SPACING = 8  # between two cards
MAX_VISIBLE = 4
VISIBLE_MS = 4000
PROBLEM_VISIBLE_MS = 7000  # warnings and errors stay longer
HOVER_GRACE_MS = 1500  # time left after the mouse leaves a toast
FADE_MS = 160
MOVE_MS = 180
SLIDE_PX = 14  # new toasts rise this much while they fade in
ICON_SIZE = 17
PADDING = 14
TEXT_WIDTH = CARD_WIDTH - 2 * PADDING - ICON_SIZE - 10 - 2  # icon, spacing and border
ACTION_WIDTH = 64  # the action button, and the space next to it

Action = tuple[str, Callable[[], None]]  # label and what it does


class Toast(QWidget):
    """One notification: the card, and around it a soft shadow painted by this widget.

    The fade uses a graphics effect on this widget; the shadow is painted by hand
    because Qt does not draw a graphics effect inside another one (a drop shadow on
    the card would leave the whole notification invisible)."""

    finished = Signal(object)  # emitted with itself once it has faded out

    def __init__(
        self, parent: QWidget, kind: str, title: str, detail: str, action: Action | None = None, duration: int = 0
    ):
        super().__init__(parent)
        self.kind = kind
        self.text_width = TEXT_WIDTH - (ACTION_WIDTH + 10 if action else 0)
        self.group: str | None = None
        self.plural = ""
        self.count = 1  # notifications merged into this one
        self.details: list[str] = []
        self.closing = False
        self.setFixedWidth(WIDTH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        icon_name, tone = theme.EVENTS.get(kind, theme.EVENTS["info"])
        self.card = QFrame()
        self.card.setObjectName("toast")
        self.card.setProperty("tone", tone)

        icon = IconView(icon_name, lambda: QColor(theme.toast_colors(tone)[3]), ICON_SIZE)
        icon.setObjectName("toastIcon")
        self.title = make_label(name="toastTitle", wrap=True)
        self.detail = make_label(name="toastDetail")
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(self.title)
        texts.addWidget(self.detail)
        self._box = QHBoxLayout(self.card)
        self._box.setContentsMargins(PADDING, 12, PADDING, 12)
        self._box.setSpacing(10)
        self._box.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        self._box.addLayout(texts, 1)
        self.action: QPushButton | None = None
        if action is not None:
            label, callback = action
            self.action = QPushButton(label)
            self.action.setObjectName("toastAction")
            self.action.setFixedWidth(ACTION_WIDTH)
            self.action.setCursor(Qt.CursorShape.PointingHandCursor)
            self.action.clicked.connect(callback)
            self.action.clicked.connect(self.dismiss)
            self._box.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW, SHADOW, SHADOW)
        outer.addWidget(self.card)
        self.set_text(title, detail)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.setDuration(FADE_MS)
        self._move = QPropertyAnimation(self, b"pos", self)
        self._move.setDuration(MOVE_MS)
        self._move.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.duration = duration or (PROBLEM_VISIBLE_MS if tone in ("warning", "error") else VISIBLE_MS)

    def set_text(self, title: str, detail: str) -> None:
        self.title.setText(title.lower())  # lowercase titles, as in SpotiFLAC
        # One line of detail; the full text stays in the tooltip when it does not fit.
        elided = QFontMetrics(self.detail.font()).elidedText(detail, Qt.TextElideMode.ElideMiddle, self.text_width)
        self.detail.setText(elided)
        self.detail.setToolTip(detail if elided != detail else "")
        self.detail.setVisible(bool(detail))
        # The title wraps, so the height depends on the width; sizeHint() alone leaves gaps.
        box = self._box
        card_height = box.totalHeightForWidth(CARD_WIDTH) if box.hasHeightForWidth() else self.card.sizeHint().height()
        self.card.setFixedHeight(card_height)
        self.setFixedHeight(card_height + 2 * SHADOW)

    # --- life cycle ---------------------------------------------------------
    def appear(self, target: QPoint) -> None:
        self.move(target + QPoint(0, SLIDE_PX))
        self.show()
        self.raise_()
        self.slide_to(target)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self.restart_timer()

    def slide_to(self, target: QPoint) -> None:
        if self._move.state() == QAbstractAnimation.State.Running and self._move.endValue() == target:
            return
        self._move.stop()
        self._move.setStartValue(self.pos())
        self._move.setEndValue(target)
        self._move.start()

    def restart_timer(self, milliseconds: int | None = None) -> None:
        self._timer.start(self.duration if milliseconds is None else milliseconds)

    def dismiss(self) -> None:
        if self.closing:
            return
        self.closing = True
        self._timer.stop()
        self._fade.stop()
        self._fade.setStartValue(self._opacity.opacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(lambda: self.finished.emit(self))
        self._fade.start()

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Soft shadow under the card ("shadow-lg"): rounded rectangles that grow and fade."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        card = QRectF(self.card.geometry()).translated(0, SHADOW_OFFSET)
        strength = theme.SHADOW_ALPHA[theme.mode()]
        for spread in range(SHADOW, 0, -1):  # the outermost, faintest ring first
            color = theme.color("shadow")
            color.setAlphaF(strength * 0.12 * (1 - spread / (SHADOW + 1)) ** 2)
            painter.setBrush(color)
            painter.drawRoundedRect(card.adjusted(-spread, -spread, spread, spread), RADIUS + spread, RADIUS + spread)

    # --- mouse --------------------------------------------------------------
    def enterEvent(self, event: QEnterEvent) -> None:
        self._timer.stop()  # stays while the mouse is over it
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        if not self.closing:
            self.restart_timer(HOVER_GRACE_MS)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dismiss()
        super().mousePressEvent(event)


class ToastHost(QObject):
    """Shows toasts over `area`, stacked upwards from its bottom-right corner.
    `right` and `bottom` are the distances from the visible cards to the edges."""

    def __init__(self, area: QWidget, *, right: int = 24, bottom: int = 24):
        super().__init__(area)
        self.area = area
        self.right = right
        self.bottom = bottom
        self.toasts: list[Toast] = []  # oldest first
        area.installEventFilter(self)

    def show(
        self,
        kind: str,
        title: str,
        detail: str = "",
        *,
        group: str | None = None,
        plural: str = "",
        action: Action | None = None,
        duration: int = 0,
    ) -> Toast:
        """Shows a toast. With `group`, a visible toast of the same group absorbs this one:
        its title becomes `plural` (with {n} replaced by the count) and its detail lists
        the different details, once each. A toast with an `action` is never merged; it
        stays `duration` milliseconds (or the usual time)."""
        if group is not None and action is None:
            for toast in reversed(self.toasts):
                if toast.group == group and not toast.closing:
                    toast.count += 1
                    if detail and detail not in toast.details:
                        toast.details.append(detail)
                    toast.set_text(toast.plural.format(n=toast.count), ", ".join(toast.details))
                    toast.restart_timer()
                    self._layout()
                    return toast
        toast = Toast(self.area, kind, title, detail, action, duration)
        toast.group = group if action is None else None
        toast.plural = plural
        toast.details = [detail] if detail else []
        toast.finished.connect(self._remove)
        self.toasts.append(toast)
        live = [t for t in self.toasts if not t.closing]
        for old in live[: max(len(live) - MAX_VISIBLE, 0)]:
            old.dismiss()
        self._layout(new=toast)
        return toast

    def clear(self) -> None:
        for toast in self.toasts:
            toast.dismiss()

    def visible_toasts(self) -> list[Toast]:
        return [t for t in self.toasts if not t.closing]

    def _remove(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
        toast.deleteLater()
        self._layout()

    def _layout(self, new: Toast | None = None, animate: bool = True) -> None:
        # Positions are those of the visible cards; each widget sits SHADOW pixels further out.
        x = max(self.area.width() - self.right - CARD_WIDTH, 8) - SHADOW
        bottom = self.area.height() - self.bottom
        for toast in reversed(self.toasts):  # the newest one sits at the bottom
            card_height = toast.height() - 2 * SHADOW
            target = QPoint(x, bottom - card_height - SHADOW)
            if toast is new:
                toast.appear(target)
            elif animate:
                toast.slide_to(target)
            else:
                toast.move(target)
            bottom -= card_height + SPACING

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.area and event.type() == QEvent.Type.Resize:
            self._layout(animate=False)
        return False
