"""
Icons that follow the theme: the line icons of Lucide (see line_icons.py) and a
terminal drawn with QPainter so it can move. Only the window buttons keep the
glyphs of the Windows icon font, like every native title bar.

- IconButton: an icon with a tooltip (card actions, back, close...).
- GearButton: settings; the gear turns on hover.
- TerminalButton: logs; a small terminal whose prompt moves and whose cursor
  blinks on hover, with a red badge for unseen warnings and errors.
- IconView and IconBadge: a plain icon, and an icon on a soft yellow circle.
- WindowButton: minimize, maximize/restore and close of the title bar.
- ToggleSwitch: animated on/off switch.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QEnterEvent, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QAbstractButton, QToolTip, QWidget

from ui import line_icons, theme


class AnimatedIconButton(QAbstractButton):
    """Icon button whose `progress` goes from 0 to 1 while the mouse is over it.

    Subclasses draw the icon in paint_icon(); the tooltip shows right away on hover.
    """

    def __init__(
        self, tooltip: str, size: tuple[int, int] = (36, 32), duration: int = 220, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setFixedSize(*size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.active = False  # drawn with the accent color (e.g. while its page is shown)
        self.danger = False  # red on hover, for destructive actions
        self._progress = 0.0
        self._animation = QPropertyAnimation(self, b"progress", self)
        self._animation.setDuration(duration)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, value: float) -> None:
        self._progress = value
        self.update()

    progress = Property(float, _get_progress, _set_progress)

    def set_active(self, active: bool) -> None:
        self.active = active
        self.update()

    def _animate_to(self, value: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._progress)
        self._animation.setEndValue(value)
        self._animation.start()

    def enterEvent(self, event: QEnterEvent) -> None:
        if self.isEnabled():
            self._animate_to(1.0)
            if self.toolTip():
                QToolTip.showText(self.mapToGlobal(QPoint(0, self.height() + 4)), self.toolTip(), self)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._animate_to(0.0)
        QToolTip.hideText()
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self._animation.stop()
            self._set_progress(0.0)
        super().changeEvent(event)

    def icon_color(self) -> QColor:
        if not self.isEnabled():
            return theme.color("disabled")
        if self.active:
            return theme.color("accent")
        if self.danger and self._progress > 0.5:
            return theme.color("danger")
        return theme.color("text") if self._progress > 0.5 or self.isDown() else theme.color("muted")

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._progress > 0:
            background = theme.color("danger_hover" if self.danger else "surface_hover")
            background.setAlphaF(self._progress)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(QRectF(self.rect()), 8, 8)
        self.paint_icon(painter, QRectF(self.rect()).center(), self.icon_color())

    def paint_icon(self, painter: QPainter, center: QPointF, color: QColor) -> None:
        raise NotImplementedError


def draw_glyph(painter: QPainter, center: QPointF, glyph: str, size: int, color: QColor) -> None:
    """Draws an icon of the Windows icon font centered on `center` (the window buttons)."""
    painter.setFont(theme.icon_font(size))
    painter.setPen(color)
    rect = QRectF(center.x() - size, center.y() - size, 2 * size, 2 * size)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)


class IconButton(AnimatedIconButton):
    """A line icon with a tooltip; `danger` turns it red on hover (delete)."""

    def __init__(
        self,
        icon: str,
        tooltip: str,
        *,
        size: tuple[int, int] = (34, 34),
        icon_size: int = 16,
        danger: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(tooltip, size=size, duration=160, parent=parent)
        self.icon_name = icon
        self.icon_size = icon_size
        self.danger = danger

    def paint_icon(self, painter: QPainter, center: QPointF, color: QColor) -> None:
        line_icons.paint(painter, self.icon_name, _square(center, self.icon_size), color)


class GearButton(AnimatedIconButton):
    """Settings button: the gear turns on hover."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Settings (Ctrl+,)", size=(38, 32), duration=500, parent=parent)

    def paint_icon(self, painter: QPainter, center: QPointF, color: QColor) -> None:
        line_icons.paint(painter, "settings", _square(center, 17), color, angle=self._progress * 90)


class IconView(QWidget):
    """A line icon in a color of the theme, read when it is painted (so it follows theme changes)."""

    def __init__(self, icon: str, color: Callable[[], QColor], size: int = 16, parent: QWidget | None = None):
        super().__init__(parent)
        self.icon_name = icon
        self.color = color
        self.setFixedSize(size, size)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        line_icons.paint(painter, self.icon_name, QRectF(self.rect()), self.color())


class IconBadge(QWidget):
    """A line icon on a soft yellow circle (the empty profile list)."""

    def __init__(self, icon: str, size: int = 64, parent: QWidget | None = None):
        super().__init__(parent)
        self.icon_name = icon
        self.setFixedSize(size, size)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("accent_soft"))
        painter.drawEllipse(QRectF(self.rect()))
        line_icons.paint(
            painter,
            self.icon_name,
            _square(QRectF(self.rect()).center(), self.width() * 0.42),
            theme.color("accent_text"),
        )


def _square(center: QPointF, size: float) -> QRectF:
    return QRectF(center.x() - size / 2, center.y() - size / 2, size, size)


class TerminalButton(AnimatedIconButton):
    """Logs button: a small terminal. On hover its prompt moves forward and the cursor
    blinks; a red badge counts the warnings and errors not seen yet."""

    BLINK_MS = 420

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Logs (Ctrl+L)", size=(42, 34), parent=parent)
        self.badge = 0
        self._cursor_visible = True
        self._blink = QTimer(self)
        self._blink.setInterval(self.BLINK_MS)
        self._blink.timeout.connect(self._toggle_cursor)

    def set_badge(self, count: int) -> None:
        self.badge = count
        self.update()

    def _toggle_cursor(self) -> None:
        self._cursor_visible = not self._cursor_visible
        self.update()

    def enterEvent(self, event: QEnterEvent) -> None:
        self._blink.start()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._blink.stop()
        self._cursor_visible = True
        super().leaveEvent(event)

    def paint_icon(self, painter: QPainter, center: QPointF, color: QColor) -> None:
        width, height = 20.0, 16.0
        frame = QRectF(center.x() - width / 2, center.y() - height / 2, width, height)
        painter.setPen(QPen(color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(frame, 3.5, 3.5)
        pen = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        # ">" prompt, moving forward a little on hover.
        x = frame.left() + 4.5 + self._progress * 1.5
        y = frame.center().y()
        painter.drawPolyline([QPointF(x, y - 3), QPointF(x + 3, y), QPointF(x, y + 3)])
        # "_" cursor, that grows and blinks while the mouse is over the button.
        if self._cursor_visible or self._progress < 0.5:
            cursor_x = x + 5.5
            painter.drawLine(QPointF(cursor_x, y + 3), QPointF(cursor_x + 3 + self._progress * 2, y + 3))
        if self.badge:
            self._paint_badge(painter)

    def _paint_badge(self, painter: QPainter) -> None:
        """Red pill with the count, in the top-right corner of the button."""
        text = str(self.badge) if self.badge < 100 else "99+"
        font = QFont(theme.FONTS.mono)
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        width = max(14.0, painter.fontMetrics().horizontalAdvance(text) + 8.0)
        rect = QRectF(self.width() - width, 0, width, 14)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("danger_fill"))
        painter.drawRoundedRect(rect, 7, 7)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


class WindowButton(QAbstractButton):
    """Minimize, maximize/restore or close button of the title bar, with the Windows glyphs."""

    TOOLTIPS: ClassVar[dict[str, str]] = {"minimize": "Minimize", "maximize": "Maximize", "close": "Close"}

    def __init__(self, kind: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = kind  # minimize, maximize or close
        self.maximized = False  # the maximize button then shows "restore"
        self.setFixedSize(46, 36)
        self.setToolTip(self.TOOLTIPS[kind])
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_maximized(self, maximized: bool) -> None:
        self.maximized = maximized
        if self.kind == "maximize":
            self.setToolTip("Restore" if maximized else "Maximize")
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        pressed, hovered = self.isDown(), self.underMouse()
        if self.kind == "close" and (hovered or pressed):
            painter.fillRect(self.rect(), QColor("#c42b1c" if pressed else "#e81123"))
            color = QColor("#ffffff")
        else:
            if hovered or pressed:
                painter.fillRect(self.rect(), theme.color("segment_selected" if pressed else "surface_hover"))
            color = theme.color("text")
        glyph = "restore" if self.kind == "maximize" and self.maximized else self.kind
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_glyph(painter, QRectF(self.rect()).center(), theme.icon(glyph), 10, color)

    def enterEvent(self, event: QEnterEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.update()
        super().leaveEvent(event)


class ToggleSwitch(QAbstractButton):
    """Animated on/off switch; yellow when on."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._offset = 1.0 if checked else 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self._animate)

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        painter.setPen(Qt.PenStyle.NoPen if on else QPen(theme.color("border_strong"), 1))
        painter.setBrush(theme.color("primary") if on else theme.color("segment"))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 11, 11)
        diameter = self.height() - 8
        x = 4 + self._offset * (self.width() - diameter - 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("primary_text") if on else theme.color("muted"))
        painter.drawEllipse(QRectF(x, 4, diameter, diameter))
