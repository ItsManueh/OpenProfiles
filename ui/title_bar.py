"""
Custom title bar of the main window: settings and the window buttons at the top
right, and the centered app name with its release badge and subtitle.

Dragging any empty part of it moves the window with the native move loop (so
Aero Snap works) and double-clicking it maximizes or restores the window.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from about import APP_NAME, APP_VERSION, RELEASES_URL, is_newer
from ui.icons import GearButton, WindowButton
from ui.widgets import make_label, restyle

log = logging.getLogger("app.gui")

CAPTION_HEIGHT = 36  # the top row, with the settings and window buttons
SUBTITLE = "Browser Anti-Detection with Multi-Profile for Social Media"


class TitleBar(QWidget):
    settings_clicked = Signal()

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.window_ = window
        self.release_url = RELEASES_URL

        self.settings_button = GearButton()
        self.settings_button.clicked.connect(self.settings_clicked)
        self.minimize_button = WindowButton("minimize")
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button = WindowButton("maximize")
        self.maximize_button.clicked.connect(self.toggle_maximized)
        self.close_button = WindowButton("close")
        self.close_button.clicked.connect(window.close)
        caption = QHBoxLayout()
        caption.setContentsMargins(0, 0, 0, 0)
        caption.setSpacing(0)
        caption.addStretch(1)
        caption.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        caption.addSpacing(10)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            caption.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)

        self.release_badge = QPushButton(f"v{APP_VERSION}")
        self.release_badge.setObjectName("releaseBadge")
        self.release_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.release_badge.setToolTip("Checking the latest release on GitHub…")
        self.release_badge.clicked.connect(self.open_releases)
        name = QHBoxLayout()
        name.setSpacing(10)
        name.addStretch(1)
        name.addWidget(make_label(APP_NAME, name="title"))
        name.addWidget(self.release_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        name.addStretch(1)
        subtitle = make_label(SUBTITLE, name="subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        caption_row = QWidget()
        caption_row.setFixedHeight(CAPTION_HEIGHT)
        caption_row.setLayout(caption)
        layout.addWidget(caption_row)
        layout.addSpacing(2)
        layout.addLayout(name)
        layout.addSpacing(2)
        layout.addWidget(subtitle)

    # --- window -------------------------------------------------------------
    def toggle_maximized(self) -> None:
        if self.window_.isMaximized():
            self.window_.showNormal()
        else:
            self.window_.showMaximized()

    def update_maximized(self) -> None:
        self.maximize_button.set_maximized(self.window_.isMaximized())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Buttons keep their own clicks; presses on labels and empty space arrive here.
        if event.button() == Qt.MouseButton.LeftButton:
            self.window_.windowHandle().startSystemMove()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # --- release badge ------------------------------------------------------
    def set_release(self, tag: str, url: str) -> None:
        """Shows the latest release published on GitHub."""
        self.release_url = url
        self.release_badge.setText(tag)
        newer = is_newer(tag)
        self.release_badge.setProperty("update", newer)
        if newer:
            self.release_badge.setToolTip(f"New release available: {tag} (you have v{APP_VERSION}). Click to see it.")
        else:
            self.release_badge.setToolTip(f"Latest release: {tag}. You are up to date. Click to see it on GitHub.")
        restyle(self.release_badge)

    def release_unavailable(self, _error: str = "") -> None:
        self.release_badge.setToolTip("Could not reach GitHub. Click to open the releases page.")

    def open_releases(self) -> None:
        if QDesktopServices.openUrl(QUrl(self.release_url)):
            log.info("Opened the releases page: %s", self.release_url)
        else:
            log.error("Could not open the releases page: %s", self.release_url)
