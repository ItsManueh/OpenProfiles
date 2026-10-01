"""Reusable widgets with the app's style: buttons, labels, segmented control, status dot and profile counter."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFocusEvent, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

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


class SegmentedControl(QWidget):
    """Mutually exclusive options in one pill. The selected option sits on a thumb that
    slides to the option picked. It is painted by hand, so the options keep their size
    whatever is selected. Keyboard: Tab focuses it, the arrow keys change the option."""

    changed = Signal(str)  # emitted only when the user picks a different option

    PADDING = 3  # between the pill and the thumb
    SLIDE_MS = 180

    def __init__(self, options: list[str], *, small: bool = False, expand: bool = False, parent: QWidget | None = None):
        """expand=True makes it as wide as its space; the options always share the width equally."""
        super().__init__(parent)
        self.setObjectName("segmented")
        self.options = list(options)
        self.small = small
        self._index = -1
        self._hover = -1
        self._position = 0.0  # where the thumb is, in options (animated)
        self._focus_ring = False  # shown only when it was reached with the keyboard
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        horizontal = QSizePolicy.Policy.Expanding if expand else QSizePolicy.Policy.Fixed
        self.setSizePolicy(horizontal, QSizePolicy.Policy.Fixed)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(self.SLIDE_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_slide)

    # --- value --------------------------------------------------------------
    def value(self) -> str:
        return self.options[self._index] if self._index >= 0 else ""

    def set_value(self, option: str) -> None:
        """Selects an option without emitting `changed`."""
        if option not in self.options:
            raise ValueError(f"Unknown option: {option}")
        self._select(self.options.index(option), emit=False)

    def _select(self, index: int, *, emit: bool) -> None:
        if index == self._index or not 0 <= index < len(self.options):
            return  # picking the selected option again changes nothing
        previous = self._index
        self._index = index
        self._animation.stop()
        if previous >= 0 and self.isVisible():
            self._animation.setStartValue(self._position)
            self._animation.setEndValue(float(index))
            self._animation.start()
        else:
            self._position = float(index)
        self.update()
        if emit:
            self.changed.emit(self.options[index])

    def _on_slide(self, value: object) -> None:
        self._position = value if isinstance(value, float) else float(self._index)
        self.update()

    # --- geometry -----------------------------------------------------------
    def _text_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(13 if self.small else 14)
        font.setWeight(QFont.Weight.Medium)
        return font

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self._text_font())
        widest = max((metrics.horizontalAdvance(option) for option in self.options), default=0)
        segment = widest + (24 if self.small else 32)
        return QSize(segment * len(self.options) + 2 * self.PADDING, 32 if self.small else 36)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def segment_rect(self, index: float) -> QRectF:
        """Rectangle of an option; a fractional index is a position in between (the sliding thumb)."""
        inner = QRectF(self.rect()).adjusted(self.PADDING, self.PADDING, -self.PADDING, -self.PADDING)
        width = inner.width() / max(len(self.options), 1)
        return QRectF(inner.left() + index * width, inner.top(), width, inner.height())

    def _index_at(self, x: float) -> int:
        for index in range(len(self.options)):
            rect = self.segment_rect(index)
            if rect.left() <= x < rect.right():
                return index
        return -1

    # --- painting -----------------------------------------------------------
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = 9.0 if self.small else 10.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("segment"))
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        if self._index >= 0:
            thumb = self.segment_rect(self._position).adjusted(0.5, 0.5, -0.5, -0.5)
            painter.setPen(QPen(theme.color("border_strong"), 1))
            painter.setBrush(theme.color("segment_selected"))
            painter.drawRoundedRect(thumb, radius - self.PADDING, radius - self.PADDING)
        if self._focus_ring and self.hasFocus():
            painter.setPen(QPen(theme.color("accent"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75), radius, radius)
        painter.setFont(self._text_font())
        for index, option in enumerate(self.options):
            if not self.isEnabled():
                color = theme.color("disabled")
            elif index in (self._index, self._hover):
                color = theme.color("text")
            else:
                color = theme.color("muted")
            painter.setPen(color)
            painter.drawText(self.segment_rect(index), Qt.AlignmentFlag.AlignCenter, option)

    # --- mouse and keyboard -------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._focus_ring = False
            self._select(self._index_at(event.position().x()), emit=True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hover = self._index_at(event.position().x())
        if hover != self._hover:
            self._hover = hover
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._select(max(self._index - 1, 0), emit=True)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._select(min(self._index + 1, len(self.options) - 1), emit=True)
        elif key == Qt.Key.Key_Home:
            self._select(0, emit=True)
        elif key == Qt.Key.Key_End:
            self._select(len(self.options) - 1, emit=True)
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        self._focus_ring = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        self.update()
        super().focusOutEvent(event)


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


class ColorPicker(QWidget):
    """A row of color swatches for a profile's label; the first one means no label."""

    changed = Signal(str)
    SWATCH = 20
    GAP = 10

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.options = ["", *theme.LABEL_COLORS]
        self._index = 0
        self._hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(len(self.options) * (self.SWATCH + self.GAP) - self.GAP + 8, 36)

    def value(self) -> str:
        return self.options[self._index]

    def set_value(self, color: str) -> None:
        self._index = self.options.index(color) if color in self.options else 0
        self.update()

    def _rect(self, index: int) -> QRectF:
        top = (self.height() - self.SWATCH) / 2
        return QRectF(4 + index * (self.SWATCH + self.GAP), top, self.SWATCH, self.SWATCH)

    def _index_at(self, x: float) -> int:
        for index in range(len(self.options)):
            rect = self._rect(index).adjusted(-self.GAP / 2, 0, self.GAP / 2, 0)
            if rect.left() <= x < rect.right():
                return index
        return -1

    def _select(self, index: int) -> None:
        if 0 <= index < len(self.options) and index != self._index:
            self._index = index
            self.update()
            self.changed.emit(self.value())

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, color in enumerate(self.options):
            rect = self._rect(index)
            if index == self._index or index == self._hover:
                ring = theme.color("text") if index == self._index else theme.color("ring")
                painter.setPen(QPen(ring, 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect.adjusted(-3.5, -3.5, 3.5, 3.5))
            if color:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(theme.LABEL_COLORS[color]))
                painter.drawEllipse(rect)
            else:  # no label: an empty circle crossed out
                painter.setPen(QPen(theme.color("muted"), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
                painter.drawLine(rect.bottomLeft() + QPointF(4, -4), rect.topRight() + QPointF(-4, 4))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._select(self._index_at(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hover = self._index_at(event.position().x())
        if hover != self._hover:
            self._hover = hover
            self.setToolTip((self.options[hover] or "No label").capitalize() if hover >= 0 else "")
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._select(self._index - 1)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._select(self._index + 1)
        else:
            super().keyPressEvent(event)


class ColorTag(QWidget):
    """The colored bar at the left of a profile card (empty without a label)."""

    def __init__(self, color: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.color = color
        self.setFixedWidth(4)

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self.color in theme.LABEL_COLORS:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.LABEL_COLORS[self.color]))
            painter.drawRoundedRect(QRectF(self.rect()), 2, 2)


class ProfileCounter(QWidget):
    """Open profiles out of the total, e.g. "2 / 5", in a small rounded badge next to the
    page title. The open count turns green while any profile is open."""

    HEIGHT = 24
    PADDING = 10

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("profileCounter")
        self.opened = 0
        self.total = 0
        self.setFixedHeight(self.HEIGHT)
        self.set_counts(0, 0)

    def set_counts(self, opened: int, total: int) -> None:
        self.opened, self.total = opened, total
        self.setToolTip(f"{opened} of {total} profile{'' if total == 1 else 's'} open")
        number, rest = self._texts()
        self.setFixedWidth(self._width(number, rest) + 2 * self.PADDING)
        self.update()

    def text(self) -> str:
        return f"{self.opened} / {self.total}"

    def _texts(self) -> tuple[str, str]:
        return str(self.opened), f" / {self.total}"

    @staticmethod
    def _fonts() -> tuple[QFont, QFont]:
        number = theme.mono_font(13)
        number.setWeight(QFont.Weight.DemiBold)
        rest = theme.mono_font(13)
        return number, rest

    def _width(self, number: str, rest: str) -> int:
        number_font, rest_font = self._fonts()
        return QFontMetrics(number_font).horizontalAdvance(number) + QFontMetrics(rest_font).horizontalAdvance(rest)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("segment"))
        radius = self.height() / 2
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        number, rest = self._texts()
        number_font, rest_font = self._fonts()
        x = (self.width() - self._width(number, rest)) / 2
        baseline = (self.height() + QFontMetrics(number_font).capHeight()) / 2
        painter.setFont(number_font)
        painter.setPen(theme.color("opened") if self.opened else theme.color("text"))
        painter.drawText(QPointF(x, baseline), number)
        painter.setFont(rest_font)
        painter.setPen(theme.color("muted"))
        painter.drawText(QPointF(x + QFontMetrics(number_font).horizontalAdvance(number), baseline), rest)


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
