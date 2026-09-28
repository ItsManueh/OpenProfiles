"""Main window: header, toolbar, list of profile cards and status bar."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QMainWindow,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import launcher
import logs
from profiles import DATA_DIR, Devices, Profile, browser_label, ios_version
from ui import theme
from ui.confirm_dialog import ConfirmDialog
from ui.controller import CLOSED, CLOSING, OPENED, OPENING, AppController
from ui.log_window import LogWindow
from ui.profile_dialog import ENGINE_LABELS, MODE_LABELS, THEME_LABELS, ProfileDialog
from ui.service import FilesFuture
from ui.widgets import StatusDot, ToggleSwitch, hbox, make_button, make_label, restyle, set_role, set_variant

log = logging.getLogger("app.gui")

STATE_LABELS = {CLOSED: "Closed", OPENING: "Opening…", OPENED: "Open", CLOSING: "Closing…"}
LOG_POLL_MS = 100  # how often new log entries are moved to the logs window


def describe_profile(profile: Profile, devices: Devices) -> str:
    user_agent = profile.user_agent or devices.get(profile.device, {}).get("user_agent", "")
    if profile.mode == "desktop":
        parts = [MODE_LABELS["desktop"], browser_label(user_agent)]
    else:
        parts = [profile.device, ios_version(user_agent), ENGINE_LABELS[profile.engine]]
    return "  ·  ".join([*parts, THEME_LABELS[profile.theme]])


class ProfileCard(QFrame):
    """One profile: status, name, summary and its actions."""

    def __init__(self, window: MainWindow, profile: Profile):
        super().__init__()
        self.setObjectName("card")
        self.main = window
        self.profile = profile
        controller = window.controller

        self.dot = StatusDot()
        name = make_label(profile.name, name="profileName")
        details = make_label(describe_profile(profile, controller.devices), "muted")
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(name)
        texts.addWidget(details)

        self.state_label = make_label(role="muted")
        self.state_label.setFixedWidth(80)
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.action = make_button("Open", "primary", width=96)
        self.action.clicked.connect(self._toggle)
        self.edit = make_button("Edit", "ghost", width=76)
        self.edit.clicked.connect(lambda: window.edit_profile(profile.name))
        self.delete = make_button("Delete", "ghost_danger", width=88)
        self.delete.clicked.connect(lambda: window.ask_delete(profile.name))

        layout = QGridLayout(self)
        layout.setContentsMargins(20, 14, 14, 14)
        layout.setHorizontalSpacing(12)
        layout.addWidget(self.dot, 0, 0)
        layout.addLayout(texts, 0, 1)
        layout.setColumnStretch(1, 1)
        layout.addWidget(self.state_label, 0, 2)
        layout.addLayout(hbox(self.action, self.edit, self.delete, spacing=4), 0, 3)
        self.set_state(controller.state_of(profile.name))

    def _toggle(self) -> None:
        controller = self.main.controller
        if controller.state_of(self.profile.name) == OPENED:
            controller.close_profile(self.profile.name)
        else:
            controller.open_profile(self.profile.name)

    def set_state(self, state: str) -> None:
        ready = self.main.controller.ready
        self.dot.set_state(state)
        self.state_label.setText(STATE_LABELS.get(state, ""))
        if state == OPENED:
            self.action.setText("Close")
            set_variant(self.action, "secondary")
            self.action.setEnabled(True)
        else:
            self.action.setText("Open")
            set_variant(self.action, "primary")
            self.action.setEnabled(ready and state == CLOSED)
        self.edit.setEnabled(ready and state == CLOSED)
        self.delete.setEnabled(state == CLOSED)


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController, log_handler: logs.PanelHandler):
        super().__init__()
        self.controller = controller
        self.log_handler = log_handler
        self.cards: dict[str, ProfileCard] = {}
        self.log_window: LogWindow | None = None
        self._dialog: QDialog | None = None  # only one dialog at a time
        self._unseen_problems = 0  # warnings and errors logged while the logs window is hidden
        self._can_close = False

        self.setWindowTitle("OpenProfiles v0.1")
        self.resize(1000, 640)
        self.setMinimumSize(880, 500)
        self._build()

        controller.profiles_changed.connect(self.refresh)
        controller.state_changed.connect(self._on_state_changed)
        controller.message.connect(self.show_message)
        controller.files_requested.connect(self._choose_files)
        controller.shutdown_finished.connect(self._finish_close)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_profile)

        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._drain_logs)
        self._log_timer.start(LOG_POLL_MS)
        self.refresh()

    # --- layout -------------------------------------------------------------
    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(34, 30, 28, 12)
        root.setSpacing(0)
        self.setCentralWidget(central)

        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(make_label("OpenProfiles v0.1", name="title"))
        titles.addWidget(make_label("Browser Anti-Detection with Multi-Profile for Social Media", name="subtitle"))
        self.theme_switch = ToggleSwitch(checked=True)
        self.theme_switch.toggled.connect(self._toggle_theme)
        self.data_button = make_button("Data folder", "ghost", small=True)
        self.data_button.setToolTip(f"Open {DATA_DIR}")
        self.data_button.clicked.connect(self.open_data_folder)
        header_actions = hbox(self.data_button, 14, self.theme_switch, make_label("Dark mode"), spacing=10)
        header_actions.setAlignment(Qt.AlignmentFlag.AlignTop)
        header = hbox(None)
        header.insertLayout(0, titles)
        header.addLayout(header_actions)
        root.addLayout(header)
        root.addSpacing(24)

        self.summary = make_label(role="muted")
        self.close_all_button = make_button("Close all", width=120)
        self.close_all_button.clicked.connect(self.controller.close_all)
        self.open_all_button = make_button("Open all", width=120)
        self.open_all_button.clicked.connect(self.controller.open_all)
        self.new_button = make_button("+  New profile", "primary", width=150)
        self.new_button.clicked.connect(self.new_profile)
        root.addLayout(hbox(self.summary, None, self.close_all_button, self.open_all_button, self.new_button))
        root.addSpacing(12)

        self.list_widget = QWidget()
        self.list_widget.setObjectName("profileList")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.list_widget)
        root.addWidget(scroll, 1)
        root.addSpacing(8)

        self.status = make_label(role="small")
        self.logs_button = make_button("Logs", "ghost", width=110, small=True)
        self.logs_button.clicked.connect(self.toggle_logs)
        root.addLayout(hbox(self.status, None, self.logs_button))

    def _empty_state(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 90, 0, 0)
        layout.setSpacing(4)
        title = make_label("No profiles yet", name="emptyTitle")
        text = make_label("Create one to open an account in its own isolated browser.", "muted")
        button = make_button("+  New profile", "primary", width=150)
        button.clicked.connect(self.new_profile)
        for widget in (title, text):
            widget.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(widget)
        layout.addSpacing(12)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        return box

    # --- list ---------------------------------------------------------------
    def refresh(self) -> None:
        while (item := self.list_layout.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        self.cards.clear()
        profiles = self.controller.profiles()
        if profiles:
            for profile in profiles.values():
                card = ProfileCard(self, profile)
                self.cards[profile.name] = card
                self.list_layout.addWidget(card)
        else:
            self.list_layout.addWidget(self._empty_state())
        self.list_layout.addStretch(1)
        self._update_toolbar()

    def _update_toolbar(self) -> None:
        total = len(self.cards)
        opened = self.controller.open_count()
        text = f"{total} profile" + ("" if total == 1 else "s")
        if opened:
            text += f"  ·  {opened} open"
        self.summary.setText(text)
        ready = self.controller.ready
        any_closed = any(self.controller.state_of(name) == CLOSED for name in self.cards)
        self.new_button.setEnabled(ready)
        self.open_all_button.setEnabled(ready and any_closed)
        self.close_all_button.setEnabled(opened > 0)

    def _on_state_changed(self, name: str, state: str) -> None:
        if (card := self.cards.get(name)) is not None:
            card.set_state(state)
        self._update_toolbar()

    def show_message(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        set_role(self.status, "status-error" if error else "small")

    def open_data_folder(self) -> None:
        """Shows the folder with the profiles, sessions, browsers and logs in the file explorer."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)  # it exists already unless it was deleted by hand
        except OSError as e:
            log.error("Could not create the data folder %s: %s", DATA_DIR, e)
            self.show_message(f"Could not create the data folder: {e}", error=True)
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR))):
            log.info("Opened the data folder: %s", DATA_DIR)
        else:
            log.error("Could not open the data folder: %s", DATA_DIR)
            self.show_message("Could not open the data folder. Its path is in the logs.", error=True)

    def _toggle_theme(self, dark: bool) -> None:
        mode = "dark" if dark else "light"
        theme.apply_theme(mode)
        restyle(self.logs_button)
        log.debug("Interface theme: %s", mode)

    # --- dialogs ------------------------------------------------------------
    def _show_dialog(self, create: Callable[[], QDialog]) -> QDialog | None:
        """Opens a window-modal dialog, or brings the current one to the front."""
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return None
        dialog = create()
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.finished.connect(self._on_dialog_finished)
        self._dialog = dialog
        dialog.open()
        return dialog

    def _on_dialog_finished(self, _result: int) -> None:
        self._dialog = None

    def new_profile(self) -> None:
        if self.controller.ready:
            log.debug("New profile dialog opened")
            self._show_dialog(lambda: ProfileDialog(self.controller, parent=self))

    def edit_profile(self, name: str) -> None:
        if not self.controller.ready:
            return
        if self.controller.state_of(name) != CLOSED:
            log.warning("Tried to edit '%s' while it is open", name)
            self.show_message("Close the profile before editing it.", error=True)
            return
        profile = self.controller.profiles().get(name)
        if profile is None:
            log.warning("Profile '%s' no longer exists; refreshing the list", name)
            self.refresh()
            return
        log.debug("Edit dialog opened for '%s'", name)
        self._show_dialog(lambda: ProfileDialog(self.controller, profile, parent=self))

    def ask_delete(self, name: str) -> None:
        if self.controller.state_of(name) != CLOSED:
            self.controller.delete_profile(name)  # shows why it cannot be deleted
            return
        message = f'"{name}" will be deleted together with its session and data. This cannot be undone.'
        dialog = self._show_dialog(lambda: ConfirmDialog("Delete profile", message, "Delete", parent=self))
        if dialog is not None:
            dialog.accepted.connect(lambda: self.controller.delete_profile(name))

    def _choose_files(self, multiple: bool, accept: str, future: FilesFuture) -> None:
        """File chooser requested by a page in WebKit (see launcher.py)."""
        filters = ";;".join(f"{label} ({patterns})" for label, patterns in launcher.file_types(accept))
        try:
            if multiple:
                paths, _ = QFileDialog.getOpenFileNames(self, "Select files to upload", "", filters)
            else:
                path, _ = QFileDialog.getOpenFileName(self, "Select files to upload", "", filters)
                paths = [path] if path else []
        except Exception as e:  # shown in the interface
            log.exception("The file chooser could not be opened")
            self.show_message(f"The file chooser could not be opened: {e}", error=True)
            paths = []
        if not future.done():
            future.set_result(paths)

    # --- logs ---------------------------------------------------------------
    def _logs_visible(self) -> bool:
        return self.log_window is not None and self.log_window.isVisible()

    def toggle_logs(self) -> None:
        if self._logs_visible():
            self.hide_logs()
        else:
            self.show_logs()

    def show_logs(self) -> None:
        # Created on first use; afterwards it is only hidden and shown again.
        if self.log_window is None:
            self.log_window = LogWindow(self.log_handler)
            self.log_window.visibility_changed.connect(self._on_logs_visibility)
        self._unseen_problems = 0
        self.log_window.show_near(self)

    def hide_logs(self) -> None:
        if self.log_window is not None:
            self.log_window.close()  # closing only hides it

    def _on_logs_visibility(self, _visible: bool) -> None:
        self._update_logs_button()

    def _update_logs_button(self) -> None:
        if self._logs_visible():
            text = "Hide logs"
        elif self._unseen_problems:
            text = f"Logs  ● {self._unseen_problems}"
        else:
            text = "Logs"
        self.logs_button.setText(text)
        self.logs_button.setProperty("alert", bool(self._unseen_problems) and not self._logs_visible())
        restyle(self.logs_button)

    def _drain_logs(self) -> None:
        entries = self.log_handler.drain()
        if not entries:
            return
        # Before the window exists the entries stay in the handler; it shows them when created.
        if self.log_window is not None:
            self.log_window.append(entries)
        if not self._logs_visible():
            problems = sum(1 for level, _ in entries if level >= logging.WARNING)
            if problems:
                self._unseen_problems += problems
                self._update_logs_button()

    # --- shutdown -----------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        """Closing first stops every profile so their sessions are saved."""
        if self._can_close:
            if self.log_window is not None:
                self.log_window.deleteLater()
            event.accept()
            return
        event.ignore()
        for button in (self.new_button, self.open_all_button, self.close_all_button):
            button.setEnabled(False)
        self.controller.shutdown()

    def _finish_close(self) -> None:
        self._can_close = True
        self._drain_logs()
        self.close()
        QApplication.quit()
