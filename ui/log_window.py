"""Real-time logs window with a level filter and copy, save and clear actions."""

from __future__ import annotations

import logging
import platform
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QFileDialog, QPlainTextEdit, QVBoxLayout, QWidget

import logs
from ui import theme
from ui.widgets import SegmentedControl, hbox, make_button, make_label

log = logging.getLogger("app.gui")

# Minimum level shown by each filter.
LOG_FILTERS = {"All": logging.DEBUG, "Info": logging.INFO, "Warnings": logging.WARNING, "Errors": logging.ERROR}
MAX_LOG_LINES = 5000


def log_color(level: int) -> str | None:
    """Color of a log entry; None keeps the theme's text color."""
    if level >= logging.ERROR:
        return theme.LOG_COLORS["error"]
    if level >= logging.WARNING:
        return theme.LOG_COLORS["warning"]
    return theme.LOG_COLORS["debug"] if level < logging.INFO else None


class LogWindow(QWidget):
    """Closing it only hides it, so the filter, the scroll position and the content are kept."""

    visibility_changed = Signal(bool)

    def __init__(self, handler: logs.PanelHandler, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("LogWindow")
        self.setWindowTitle("Logs · OpenProfiles")
        self.setMinimumSize(620, 280)
        self.resize(920, 440)
        self.handler = handler
        self.min_level = logging.INFO

        self.filter = SegmentedControl(list(LOG_FILTERS), small=True)
        self.filter.set_value("Info")
        self.filter.changed.connect(self._on_filter_change)
        copy = make_button("Copy", small=True)
        copy.clicked.connect(self.copy)
        save = make_button("Save", small=True)
        save.clicked.connect(self.save)
        clear = make_button("Clear", small=True)
        clear.clicked.connect(self.clear)

        self.view = QPlainTextEdit()
        self.view.setObjectName("logView")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.view.setMaximumBlockCount(MAX_LOG_LINES)  # the oldest lines are dropped
        self.view.setFont(theme.mono_font(12))
        self.feedback = make_label(role="small")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 10)
        root.setSpacing(10)
        root.addLayout(hbox(self.filter, None, copy, save, clear, spacing=6))
        root.addWidget(self.view, 1)
        root.addWidget(self.feedback)
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

    def closeEvent(self, event: QCloseEvent) -> None:
        event.ignore()
        self.hide()
        self.visibility_changed.emit(False)

    # --- content ------------------------------------------------------------
    def append(self, entries: list[tuple[int, str]]) -> None:
        visible = [entry for entry in entries if entry[0] >= self.min_level]
        if not visible:
            return
        scroll = self.view.verticalScrollBar()
        follow = scroll.value() >= scroll.maximum() - 2  # only auto-scroll when already at the bottom
        cursor = QTextCursor(self.view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for level, line in visible:
            if not self.view.document().isEmpty():
                cursor.insertBlock()
            text_format = QTextCharFormat()
            if (color := log_color(level)) is not None:
                text_format.setForeground(QColor(color))
            cursor.insertText(line, text_format)
        if follow:
            scroll.setValue(scroll.maximum())

    def _render(self) -> None:
        self.view.clear()
        self.append(self.handler.snapshot())

    def _on_filter_change(self, label: str) -> None:
        self.min_level = LOG_FILTERS[label]
        self._render()

    # --- actions ------------------------------------------------------------
    def copy(self) -> None:
        lines = [line for _, line in self.handler.records(self.min_level)]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.feedback.setText(f"Copied {len(lines)} entries to the clipboard.")

    def save(self) -> None:
        """Saves every entry (all levels) to a plain-text file."""
        default = str(Path.home() / f"openprofiles-{time.strftime('%Y%m%d-%H%M%S')}.log")
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save logs", default, "Log files (*.log);;Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        lines = [line for _, line in self.handler.records()]
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
            self.feedback.setText(f"Could not save the logs: {e}")
            return
        log.info("Saved %d log entries to %s", len(lines), path)
        self.feedback.setText(f"Saved {len(lines)} entries to {path}")

    def clear(self) -> None:
        self.handler.clear()
        self._render()
        self.feedback.setText("Logs cleared.")
