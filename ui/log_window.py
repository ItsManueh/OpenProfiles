"""Real-time logs window: level filter, search, and copy, save and clear actions.

Each line shows the time, a dot and the message. The dot has the color of the
notification when the entry is a user-visible event (profile created, opened...),
of warnings and errors for those, and is small and grey otherwise. Copy and Save
export the full lines of app.log.
"""

from __future__ import annotations

import logging
import platform
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont, QGuiApplication, QIcon, QShowEvent, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFileDialog, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

import logs
from ui import frame, line_icons, theme
from ui.title_bar import WindowHeader
from ui.toast import ToastHost
from ui.widgets import SegmentedControl, hbox, make_button, make_label

log = logging.getLogger("app.gui")

# Minimum level shown by each filter.
LOG_FILTERS = {"All": logging.DEBUG, "Info": logging.INFO, "Warnings": logging.WARNING, "Errors": logging.ERROR}
MAX_LOG_LINES = 5000
SEARCH_DELAY_MS = 180  # the search waits for a pause in typing before filtering
INDENT = " " * 14  # continuation lines (tracebacks) start under the message


EVENT_MARKER = "●"  # events, warnings and errors, in the color of their tone


def entry_marker(entry: logs.LogEntry) -> tuple[str, str | None]:
    """(marker, tone) of the event or of the level; plain lines get a small grey dot and no tone."""
    if entry.event in theme.EVENTS:
        kind = entry.event
    elif entry.level >= logging.CRITICAL:
        kind = "critical"
    elif entry.level >= logging.ERROR:
        kind = "error"
    elif entry.level >= logging.WARNING:
        kind = "warning"
    else:
        return ("·" if entry.level < logging.INFO else "•"), None
    return EVENT_MARKER, theme.EVENTS[kind][1]


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


class _Formats:
    """Text formats of the log lines, created once instead of once per line."""

    def __init__(self) -> None:
        self.muted = self._colored(theme.LOG_COLORS["debug"])
        self.markers = {tone: self._colored(color) for tone, color in theme.LOG_TONE_COLORS.items()}
        self.plain = QTextCharFormat()
        self.debug = self._colored(theme.LOG_COLORS["debug"])
        self.warning = self._colored(theme.LOG_COLORS["warning"])
        self.warning.setFontWeight(QFont.Weight.DemiBold)
        self.error = self._colored(theme.LOG_COLORS["error"])
        self.error.setFontWeight(QFont.Weight.DemiBold)

    @staticmethod
    def _colored(color: str) -> QTextCharFormat:
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color))
        return text_format

    def message(self, level: int) -> QTextCharFormat:
        if level >= logging.ERROR:
            return self.error
        if level >= logging.WARNING:
            return self.warning
        return self.debug if level < logging.INFO else self.plain


class LogWindow(QWidget):
    """Closing it only hides it, so the filter, the search, the scroll position and the content are kept."""

    visibility_changed = Signal(bool)

    def __init__(
        self, handler: logs.PanelHandler, diagnostics: Callable[[], str] | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self.diagnostics = diagnostics
        self.header = WindowHeader(self, "Logs")
        self.setObjectName("LogWindow")
        self.setWindowTitle("Logs · OpenProfiles")
        self.setMinimumSize(680, 300)
        self.resize(940, 460)
        self.handler = handler
        self.min_level = logging.INFO
        self.query = ""
        self.shown = 0
        self._formats = _Formats()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self._apply_search)

        self.filter = SegmentedControl(list(LOG_FILTERS), small=True)
        self.filter.set_value("Info")
        self.filter.changed.connect(self._on_filter_change)
        self.search = QLineEdit()
        self.search.setObjectName("logSearch")
        self.search.setPlaceholderText("Search")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(
            QIcon(line_icons.pixmap("search", QColor(theme.LOG_COLORS["debug"]), 14)),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self._on_search)
        copy = make_button("Copy", small=True)
        copy.clicked.connect(self.copy)
        save = make_button("Save", small=True)
        save.clicked.connect(self.save)
        clear = make_button("Clear", small=True)
        clear.clicked.connect(self.clear)
        report = make_button("Diagnostics", small=True)
        report.setToolTip("Copy a summary to paste when reporting a problem")
        report.clicked.connect(self.copy_diagnostics)
        report.setVisible(diagnostics is not None)

        self.view = QPlainTextEdit()
        self.view.setObjectName("logView")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMaximumBlockCount(MAX_LOG_LINES)  # the oldest lines are dropped
        self.view.setFont(theme.mono_font(12))
        self.summary = make_label(role="small")

        content = QVBoxLayout()
        content.setContentsMargins(16, 4, 16, 10)
        content.setSpacing(10)
        content.addLayout(hbox(self.filter, self.search, None, report, 6, copy, save, clear, spacing=6))
        content.addWidget(self.view, 1)
        content.addWidget(self.summary)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header)
        root.addLayout(content, 1)
        self.toasts = ToastHost(self, right=16, bottom=40)
        frame.make_frameless(self)  # its own title bar, like the main window
        self._render()

    # --- window -------------------------------------------------------------
    def show_near(self, anchor: QWidget) -> None:
        """Shows the window; the first time, over the bottom-right corner of `anchor`."""
        if not self.property("placed"):
            corner = anchor.frameGeometry().bottomRight()
            self.move(max(corner.x() - self.width() - 24, 0), max(corner.y() - self.height() - 24, 0))
            self.setProperty("placed", True)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.visibility_changed.emit(True)

    def showEvent(self, event: QShowEvent) -> None:
        frame.set_border_color(self, theme.color("border"))
        super().showEvent(event)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.StyleChange and self.isVisible():
            frame.set_border_color(self, theme.color("border"))  # the theme changed
        elif event.type() == QEvent.Type.WindowStateChange:
            self.header.update_maximized()
        return super().event(event)

    def nativeEvent(self, eventType: QByteArray | bytes | bytearray | memoryview, message: int) -> object:
        name = eventType.data() if isinstance(eventType, QByteArray) else bytes(eventType)
        if name == b"windows_generic_MSG":
            result = frame.handle_message(self, int(message))
            if result is not None:
                return True, result
        return super().nativeEvent(eventType, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()
        self.visibility_changed.emit(False)

    # --- content ------------------------------------------------------------
    def append(self, entries: list[logs.LogEntry]) -> None:
        visible = [entry for entry in entries if entry.matches(self.min_level, self.query)]
        if visible:
            scroll = self.view.verticalScrollBar()
            follow = scroll.value() >= scroll.maximum() - 2  # only auto-scroll when already at the bottom
            cursor = QTextCursor(self.view.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.beginEditBlock()  # one layout pass for the whole batch
            for entry in visible:
                self._insert(cursor, entry)
            cursor.endEditBlock()
            if follow:
                scroll.setValue(scroll.maximum())
            self.shown += len(visible)
        self._update_summary()

    def _insert(self, cursor: QTextCursor, entry: logs.LogEntry) -> None:
        formats = self._formats
        marker, tone = entry_marker(entry)
        first, *rest = entry.message.split("\n")
        if not self.view.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText(time.strftime("%H:%M:%S", time.localtime(entry.created)) + "  ", formats.muted)
        cursor.insertText(marker, formats.muted if tone is None else formats.markers[tone])
        cursor.insertText("  " + first, formats.message(entry.level))
        for line in rest:  # traceback
            cursor.insertBlock()
            cursor.insertText(INDENT + line, formats.muted)

    def _render(self) -> None:
        self.view.clear()
        self.shown = 0
        self.append(self.handler.snapshot())

    def _update_summary(self) -> None:
        count = self.handler.count()
        total = _plural(count, "entry", "entries")
        self.summary.setText(total if self.shown == count else f"{self.shown} of {total}")

    def _on_filter_change(self, label: str) -> None:
        self.min_level = LOG_FILTERS[label]
        self._render()

    def _on_search(self, _text: str) -> None:
        self._search_timer.start()  # restarted by every key; filters once typing pauses

    def _apply_search(self) -> None:
        query = self.search.text().strip().lower()
        if query != self.query:
            self.query = query
            self._render()

    def _flush_search(self) -> None:
        """Applies a search still waiting for its delay (e.g. Copy pressed right after typing)."""
        if self._search_timer.isActive():
            self._search_timer.stop()
            self._apply_search()

    # --- actions ------------------------------------------------------------
    def copy(self) -> None:
        self._flush_search()
        lines = [entry.line for entry in self.handler.records(self.min_level, self.query)]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.toasts.show("copied", "Copied to the clipboard", _plural(len(lines), "line", "lines"))

    def copy_diagnostics(self) -> None:
        if self.diagnostics is None:
            return
        QGuiApplication.clipboard().setText(self.diagnostics())
        self.toasts.show("copied", "Diagnostics copied", "Versions, browsers, settings and the latest problems")

    def save(self) -> None:
        """Saves every entry (all levels, ignoring the search) to a plain-text file."""
        default = str(Path.home() / f"openprofiles-{time.strftime('%Y%m%d-%H%M%S')}.log")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save logs", default, "Log files (*.log);;Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        lines = [entry.line for entry in self.handler.records()]
        header = [
            f"OpenProfiles log, saved {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Python {platform.python_version()} on {platform.platform()}",
            f"{len(lines)} entries",
            "",
        ]
        try:
            Path(path).write_text("\n".join(header + lines) + "\n", encoding="utf-8")
        except OSError as e:
            log.error("Could not save the logs to %s: %s", path, e)
            self.toasts.show("error", "Could not save the logs", str(e))
            return
        log.info("Saved %d log entries to %s", len(lines), path, extra={"event": "saved"})
        self.toasts.show("saved", "Logs saved", path)

    def clear(self) -> None:
        self.handler.clear()
        self._render()
        self.toasts.show("cleared", "Logs cleared")
