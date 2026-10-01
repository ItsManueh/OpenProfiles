"""
Dialogs without the Windows frame: a card with its own header (title, optional
subtitle and a close button) that keeps the native shadow and rounded corners,
opens centered on the window (it grows and shrinks from its center), moves by
dragging any empty part of it, and dims the window behind it.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QByteArray, QEvent, QObject, QPoint, QPropertyAnimation, QRect, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QMouseEvent, QPainter, QPaintEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLayout, QVBoxLayout, QWidget

from ui import frame, theme
from ui.icons import IconButton
from ui.widgets import make_label

BACKDROP_OPACITY = {"dark": 0.55, "light": 0.3}
BACKDROP_FADE_MS = 150


class Dialog(QDialog):
    """Base of the app's dialogs. Subclasses add their content to `self.body`.

    Escape and the close button reject the dialog."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        frame.make_frameless(self, resizable=False)
        self._placed = False  # centered once it has its final size

        self.close_button = IconButton("x", "Close (Esc)", size=(30, 30), icon_size=16)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.clicked.connect(self.reject)
        texts = QVBoxLayout()
        texts.setContentsMargins(0, 4, 0, 0)
        texts.setSpacing(4)
        texts.addWidget(make_label(title, name="dialogTitle"))
        if subtitle:
            texts.addWidget(make_label(subtitle, "muted"))
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)
        header.addLayout(texts, 1)
        header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 20, 20, 24)
        root.setSpacing(0)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)  # the dialog always fits its content
        root.addLayout(header)
        root.addSpacing(18)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 8, 0)  # the close button sits closer to the edge than the content
        self.body.setSpacing(0)
        root.addLayout(self.body)

    # --- native frame -------------------------------------------------------
    def showEvent(self, event: QShowEvent) -> None:
        frame.set_border_color(self, theme.color("border_strong"))
        super().showEvent(event)
        if not self._placed:
            self.adjustSize()  # the final size, before placing it
            self.move(self._centered_position())
            self._placed = True

    def _centered_position(self) -> QPoint:
        """Centered on the app's window (or on the screen under the mouse), inside the screen."""
        parent = self.parentWidget()
        if parent is not None and parent.window().isVisible():
            anchor = parent.window().frameGeometry().center()
        else:
            anchor = QCursor.pos()
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        if parent is None:
            anchor = area.center()
        box = QRect(QPoint(0, 0), self.frameSize())
        box.moveCenter(anchor)
        x = min(max(box.left(), area.left()), area.right() - box.width())
        y = min(max(box.top(), area.top()), area.bottom() - box.height())
        return QPoint(x, y)

    def resizeEvent(self, event: QResizeEvent) -> None:
        # Showing or hiding fields changes the size: keep the same center.
        old = event.oldSize()
        if self._placed and self.isVisible() and old.isValid():
            delta = event.size() - old
            self.move(self.x() - delta.width() // 2, self.y() - delta.height() // 2)
        super().resizeEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.StyleChange and self.isVisible():
            frame.set_border_color(self, theme.color("border_strong"))  # the theme changed
        return super().event(event)

    def nativeEvent(self, eventType: QByteArray | bytes | bytearray | memoryview, message: int) -> object:
        name = eventType.data() if isinstance(eventType, QByteArray) else bytes(eventType)
        if name == b"windows_generic_MSG":
            result = frame.handle_message(self, int(message), resizable=False)
            if result is not None:
                return True, result
        return super().nativeEvent(eventType, message)

    # --- moving -------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Fields and buttons keep their clicks; presses on labels and empty space move the dialog.
        if event.button() == Qt.MouseButton.LeftButton:
            self.windowHandle().startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)


class Backdrop(QWidget):
    """Dims a window while one of its dialogs is open; it fades in and out."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window_ = window
        self._level = 0.0
        self.setGeometry(window.rect())
        window.installEventFilter(self)
        self._fade = QPropertyAnimation(self, b"level", self)
        self._fade.setDuration(BACKDROP_FADE_MS)
        self.show()
        self.raise_()
        self._fade_to(1.0)

    def _get_level(self) -> float:
        return self._level

    def _set_level(self, value: float) -> None:
        self._level = value
        self.update()

    level = Property(float, _get_level, _set_level)

    def _fade_to(self, value: float) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._level)
        self._fade.setEndValue(value)
        self._fade.start()

    def dismiss(self) -> None:
        """Fades out and deletes itself."""
        self._fade.finished.connect(self.deleteLater)
        self._fade_to(0.0)

    def paintEvent(self, _event: QPaintEvent) -> None:
        shade = QColor(0, 0, 0)
        shade.setAlphaF(self._level * BACKDROP_OPACITY[theme.mode()])
        QPainter(self).fillRect(self.rect(), shade)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.window_ and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.window_.rect())
        return False
