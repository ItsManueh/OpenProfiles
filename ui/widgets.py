"""Reusable widgets with the app's style: buttons, segmented control, switch and status dot."""

from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPaintEvent
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from ui import theme


def restyle(widget: QWidget) -> None:
    """Re-applies the style sheet after changing a dynamic property."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def make_button(text: str, variant: str = "secondary", *, width: int | None = None, small: bool = False) -> QPushButton:
    """variant: primary, secondary, ghost, ghost_danger or danger."""
    button = QPushButton(text)
    button.setProperty("variant", variant)
    if small:
        button.setProperty("size", "small")
    if width:
        button.setFixedWidth(width)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)  # Enter only triggers the dialog's default button
    return button


def set_variant(button: QPushButton, variant: str) -> None:
    button.setProperty("variant", variant)
    restyle(button)


def make_label(text: str = "", role: str | None = None, *, name: str | None = None, wrap: bool = False) -> QLabel:
    """role: muted, small, field, error or status-error (see the style sheet)."""
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def set_role(label: QLabel, role: str) -> None:
    label.setProperty("role", role)
    restyle(label)


class SegmentedControl(QFrame):
    """Group of mutually exclusive options shown as one pill (like the iOS control)."""

    changed = Signal(str)

    def __init__(self, options: list[str], *, small: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("segmented")
        if small:
            self.setProperty("size", "small")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(3)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for option in options:
            button = QPushButton(option)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoDefault(False)
            self._group.addButton(button)
            layout.addWidget(button)
        self._group.buttonClicked.connect(self._on_clicked)

    def _on_clicked(self, button: QAbstractButton) -> None:
        self.changed.emit(button.text())

    def value(self) -> str:
        checked = self._group.checkedButton()
        return checked.text() if checked else ""

    def set_value(self, option: str) -> None:
        """Selects an option without emitting `changed`."""
        for button in self._group.buttons():
            if button.text() == option:
                button.setChecked(True)
                return
        raise ValueError(f"Unknown option: {option}")


class ToggleSwitch(QAbstractButton):
    """Animated on/off switch."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)
        self._offset = 1.0 if checked else 0.0
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(140)
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
        return QSize(42, 24)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("primary") if self.isChecked() else theme.color("border"))
        painter.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        diameter = self.height() - 6
        x = 3 + self._offset * (self.width() - diameter - 6)
        painter.setBrush(theme.color("switch_knob") if self.isChecked() else theme.color("switch_knob_off"))
        painter.drawEllipse(QRectF(x, 3, diameter, diameter))


class StatusDot(QWidget):
    """Small colored dot that shows whether a profile is closed, opening or open."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._state = "closed"

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        key = self._state if self._state in ("opened", "closed") else "opening"
        painter.setBrush(theme.color(key))
        painter.drawEllipse(QRectF(0, 0, self.width(), self.height()))


def hbox(*widgets: QWidget | int | None, spacing: int = 8) -> QHBoxLayout:
    """Horizontal layout; an int adds that much fixed space, None adds a stretch."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for item in widgets:
        if item is None:
            layout.addStretch(1)
        elif isinstance(item, int):
            layout.addSpacing(item)
        else:
            layout.addWidget(item)
    return layout
