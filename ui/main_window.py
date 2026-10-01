"""Main window: title bar, then either the profile list or the settings page, and the status bar."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import QCloseEvent, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import launcher
import logs
from about import APP_NAME
from profiles import DATA_DIR, Devices, Profile, browser_label, ios_version
from settings import Settings, save_settings
from ui import frame, theme
from ui.confirm_dialog import ConfirmDialog
from ui.controller import CLOSED, CLOSING, OPENED, OPENING, AppController
from ui.dialog import Backdrop
from ui.icons import IconBadge, IconButton, TerminalButton
from ui.log_window import LogWindow
from ui.profile_dialog import ENGINE_LABELS, MODE_LABELS, THEME_LABELS, ProfileDialog
from ui.release import ReleaseChecker
from ui.service import FilesFuture
from ui.settings_page import SettingsPage
from ui.sound import NotificationSound
from ui.title_bar import TitleBar
from ui.toast import ToastHost
from ui.widgets import ProfileCounter, StatusDot, hbox, make_button, make_label, restyle, set_variant

log = logging.getLogger("app.gui")

# Shown next to the name; a closed profile shows nothing (its grey dot says it).
STATE_LABELS = {CLOSED: "", OPENING: "Starting…", OPENED: "Running", CLOSING: "Saving session…"}
LOG_POLL_MS = 100  # how often new log entries are moved to the logs window
PAGE_MS = 200  # page transition
PAGE_SLIDE_PX = 14
CHOOSER_RAISE_MS = 100  # how often the file chooser is looked for, to bring it to the front
CHOOSER_RAISE_TRIES = 30
# Notification title of each profile event, alone and when several are merged into one.
# Notifications never name the profile; the logs do.
EVENT_TEXTS = {
    "created": ("Profile created", "{n} profiles created"),
    "edited": ("Changes saved", "{n} profiles edited"),
    "deleted": ("Profile deleted", "{n} profiles deleted"),
    "opened": ("Profile opened", "{n} profiles opened"),
    "closed": ("Profile closed", "{n} profiles closed"),
    "downloaded": ("Browsers downloaded", "Browsers downloaded"),
}


def describe_profile(profile: Profile, devices: Devices) -> str:
    user_agent = profile.user_agent or devices.get(profile.device, {}).get("user_agent", "")
    if profile.mode == "desktop":
        parts = [MODE_LABELS["desktop"], browser_label(user_agent)]
    else:
        parts = [profile.device, ios_version(user_agent), ENGINE_LABELS[profile.engine]]
    return "  ·  ".join([*parts, THEME_LABELS[profile.theme]])


class ProfileCard(QFrame):
    """One profile: status, name and state, summary, and its actions (open or close,
    edit and delete)."""

    def __init__(self, window: MainWindow, profile: Profile):
        super().__init__()
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)  # the border lights up on hover
        self.main = window
        self.profile = profile
        controller = window.controller

        self.dot = StatusDot()
        name = make_label(profile.name, name="profileName")
        self.state_label = make_label(name="stateLabel")
        details = make_label(describe_profile(profile, controller.devices), "muted")
        title = QHBoxLayout()
        title.setSpacing(10)
        title.addWidget(name)
        title.addWidget(self.state_label)
        title.addStretch(1)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addLayout(title)
        texts.addWidget(details)

        self.action = make_button("Open", "primary", width=92)
        self.action.clicked.connect(self._toggle)
        self.edit = IconButton("pencil", "Edit")
        self.edit.clicked.connect(lambda: window.edit_profile(profile.name))
        self.delete = IconButton("trash", "Delete", danger=True)
        self.delete.clicked.connect(lambda: window.ask_delete(profile.name))

        layout = QGridLayout(self)
        layout.setContentsMargins(20, 14, 14, 14)
        layout.setHorizontalSpacing(14)
        layout.addWidget(self.dot, 0, 0)
        layout.addLayout(texts, 0, 1)
        layout.setColumnStretch(1, 1)
        layout.addLayout(hbox(self.action, 6, self.edit, self.delete, spacing=2), 0, 2)
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
        self.state_label.setProperty("state", state)
        restyle(self.state_label)
        if state == OPENED:
            self.action.setText("Close")
            set_variant(self.action, "secondary")
            self.action.setEnabled(True)
        else:
            self.action.setText("Open")
            set_variant(self.action, "primary")
            self.action.setEnabled(ready and state == CLOSED)
        self.edit.setEnabled(ready and state == CLOSED)
        self.delete.setEnabled(state == CLOSED and not self.main.controller.closing)


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController, log_handler: logs.PanelHandler, settings: Settings):
        super().__init__()
        self.controller = controller
        self.log_handler = log_handler
        self.settings = settings
        self.cards: dict[str, ProfileCard] = {}
        self.log_window: LogWindow | None = None
        self._dialog: QDialog | None = None  # only one dialog at a time
        self._backdrop: Backdrop | None = None  # dims the window behind the dialog
        self._unseen_problems = 0  # warnings and errors logged while the logs window is hidden
        self._can_close = False
        self._page_animation: tuple[QParallelAnimationGroup, QWidget] | None = None  # running page transition
        self.sound = NotificationSound(settings.sounds)

        self.setWindowTitle(APP_NAME)
        self.resize(1000, 660)
        self.setMinimumSize(880, 540)
        self._build()
        frame.make_frameless(self)  # the title bar is drawn by the app (see TitleBar)
        theme.on_theme_changed(self._on_theme_changed)
        self._on_theme_changed(theme.mode())

        controller.profiles_changed.connect(self.refresh)
        controller.ready_changed.connect(self._on_ready_changed)
        controller.state_changed.connect(self._on_state_changed)
        controller.message.connect(self.show_message)
        controller.notified.connect(self._on_notified)
        controller.files_requested.connect(self._choose_files)
        controller.retry_available.connect(self.retry_button.setVisible)
        controller.shutdown_finished.connect(self._finish_close)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_profile)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(self.toggle_settings)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.toggle_logs)

        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._drain_logs)
        self._log_timer.start(LOG_POLL_MS)
        self.refresh()

        self.release_checker = ReleaseChecker(self)
        self.release_checker.found.connect(self._on_release_found)
        self.release_checker.failed.connect(self.title_bar.release_unavailable)
        self.release_checker.failed.connect(self.settings_page.release_unavailable)
        self.release_checker.check()

    # --- layout -------------------------------------------------------------
    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.setCentralWidget(central)
        self.title_bar = TitleBar(self)
        self.title_bar.settings_clicked.connect(self.toggle_settings)
        outer.addWidget(self.title_bar)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(34, 22, 28, 12)
        root.setSpacing(0)
        outer.addWidget(content, 1)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)

        # Home page: toolbar and profile list.
        self.home_page = QWidget()
        self.home_page.setObjectName("page")
        home = QVBoxLayout(self.home_page)
        home.setContentsMargins(0, 0, 0, 0)
        home.setSpacing(0)
        self.pages.addWidget(self.home_page)
        self.counter = ProfileCounter()
        self.close_all_button = make_button("Close all", width=104)
        self.close_all_button.clicked.connect(self.controller.close_all)
        self.open_all_button = make_button("Open all", width=104)
        self.open_all_button.clicked.connect(self.controller.open_all)
        self.new_button = make_button("New profile", "primary", width=124)
        self.new_button.setToolTip("New profile (Ctrl+N)")
        self.new_button.clicked.connect(self.new_profile)
        heading = make_label("Profiles", name="pageTitle")
        home.addLayout(
            hbox(heading, 4, self.counter, None, self.close_all_button, self.open_all_button, self.new_button)
        )
        home.addSpacing(14)

        self.list_widget = QWidget()
        self.list_widget.setObjectName("profileList")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.list_widget)
        home.addWidget(scroll, 1)

        # Settings page, in the same place.
        self.settings_page = SettingsPage(self.settings.mode, self.settings.sounds)
        self.settings_page.back_requested.connect(self.show_home)
        self.settings_page.mode_changed.connect(self._on_mode_changed)
        self.settings_page.sounds_changed.connect(self._on_sounds_changed)
        self.settings_page.data_folder_requested.connect(self.open_data_folder)
        self.settings_page.releases_requested.connect(self.title_bar.open_releases)
        self.pages.addWidget(self.settings_page)

        root.addSpacing(8)
        self.status = make_label(role="small")  # progress only: starting, downloading, closing
        self.retry_button = make_button("Retry", small=True)  # when the browsers could not start
        self.retry_button.setToolTip("Try to start the browsers again")
        self.retry_button.clicked.connect(self.controller.start)
        self.retry_button.hide()
        self.logs_button = TerminalButton()
        self.logs_button.clicked.connect(self.toggle_logs)
        root.addLayout(hbox(self.status, 4, self.retry_button, None, self.logs_button))
        self.toasts = ToastHost(central, right=28, bottom=56)  # above the status bar

    def _empty_state(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 70, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(IconBadge("users"), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(14)
        title = make_label("No profiles yet", name="emptyTitle")
        text = make_label("Create one to open an account in its own isolated browser.", "muted")
        button = make_button("New profile", "primary", width=124)
        button.setObjectName("emptyNewButton")
        button.setEnabled(self.controller.ready)
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
        self.counter.set_counts(opened, total)
        ready = self.controller.ready
        any_closed = any(self.controller.state_of(name) == CLOSED for name in self.cards)
        # With no profiles, only the button of the empty state (in the middle) creates one.
        self.new_button.setEnabled(ready and total > 0)
        self.open_all_button.setEnabled(ready and any_closed)
        self.close_all_button.setEnabled(opened > 0)

    def _on_ready_changed(self, _ready: bool) -> None:
        """The engine became available, or stopped being so (closing): update every button."""
        for name, card in self.cards.items():
            card.set_state(self.controller.state_of(name))
        empty_button = self.findChild(QPushButton, "emptyNewButton")
        if empty_button is not None:
            empty_button.setEnabled(self.controller.ready)
        self._update_toolbar()

    def _on_state_changed(self, name: str, state: str) -> None:
        if (card := self.cards.get(name)) is not None:
            card.set_state(state)
        self._update_toolbar()

    def show_message(self, text: str, error: bool = False) -> None:
        """Progress goes to the status bar; errors become notifications."""
        if error:
            self.toasts.show("error", text)
        else:
            self.status.setText(text)

    def _on_notified(self, kind: str, _name: str, detail: str) -> None:
        """Shows a notification for an event; the name of the profile is never shown."""
        self.sound.play_for(kind)  # profiles created, edited or deleted, and browsers downloaded
        if kind in EVENT_TEXTS:
            title, plural = EVENT_TEXTS[kind]
            self.toasts.show(kind, title, detail, group=kind, plural=plural)
        else:  # warning or error: what happened
            self.toasts.show(kind, detail)

    def open_data_folder(self) -> None:
        """Shows the folder with the profiles, sessions, browsers and logs in the file explorer."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)  # it exists already unless it was deleted by hand
        except OSError as e:
            log.error("Could not create the data folder %s: %s", DATA_DIR, e)
            self.toasts.show("error", "Could not create the data folder", str(e))
            return
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR))):
            log.info("Opened the data folder: %s", DATA_DIR, extra={"event": "folder"})
            self.toasts.show("folder", "Data folder opened", "Profiles, sessions, browsers and logs")
        else:
            log.error("Could not open the data folder: %s", DATA_DIR)
            self.toasts.show("error", "Could not open the data folder", str(DATA_DIR))

    # --- pages --------------------------------------------------------------
    def settings_visible(self) -> bool:
        return self.pages.currentWidget() is self.settings_page

    def toggle_settings(self) -> None:
        if self.settings_visible():
            self.show_home()
        else:
            self.show_page(self.settings_page)

    def show_home(self) -> None:
        self.show_page(self.home_page)

    def show_page(self, page: QWidget) -> None:
        """Shows a page of the window: it fades in while rising a little."""
        if self.pages.currentWidget() is page:
            return
        self._finish_page_animation()  # a quick second click cuts the previous one short
        self.pages.setCurrentWidget(page)
        self.title_bar.settings_button.set_active(page is self.settings_page)
        if page is self.settings_page:
            page.setFocus()  # Esc goes back
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity")
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        target = self.pages.contentsRect().topLeft()
        slide = QPropertyAnimation(page, b"pos")
        slide.setStartValue(target + QPoint(0, PAGE_SLIDE_PX))
        slide.setEndValue(target)
        group = QParallelAnimationGroup(self)
        for animation in (fade, slide):
            animation.setDuration(PAGE_MS)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(animation)
        group.finished.connect(self._finish_page_animation)
        self._page_animation = (group, page)
        group.start()

    def _finish_page_animation(self) -> None:
        """Leaves the animated page in its place, fully visible and without the effect
        (it would slow down scrolling)."""
        if self._page_animation is None:
            return
        group, page = self._page_animation
        self._page_animation = None
        group.stop()
        effect = page.graphicsEffect()
        if effect is not None:
            effect.setEnabled(False)
        page.move(self.pages.contentsRect().topLeft())
        group.deleteLater()

    # --- settings and window -------------------------------------------------
    def bring_to_front(self) -> None:
        """Shows the window over the others: when the app starts, and when it is opened again."""
        frame.bring_to_front(self)

    def _on_release_found(self, tag: str, url: str) -> None:
        self.title_bar.set_release(tag, url)
        self.settings_page.set_release(tag)

    def _on_mode_changed(self, mode: str) -> None:
        theme.apply_mode(mode)
        self.settings.mode = mode
        log.info("Appearance mode: %s", mode)
        self._save_settings()

    def _on_sounds_changed(self, enabled: bool) -> None:
        self.sound.enabled = enabled
        self.settings.sounds = enabled
        log.info("Notification sounds: %s", "on" if enabled else "off")
        self._save_settings()

    def _save_settings(self) -> None:
        try:
            save_settings(self.settings)
        except OSError as e:
            log.warning("Could not save the settings: %s", e)
            self.toasts.show("warning", "Could not save the settings", str(e))

    def _on_theme_changed(self, _theme: str) -> None:
        frame.set_border_color(self, theme.color("border"))

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self.title_bar.update_maximized()
        super().changeEvent(event)

    def nativeEvent(self, eventType: QByteArray | bytes | bytearray | memoryview, message: int) -> object:
        name = eventType.data() if isinstance(eventType, QByteArray) else bytes(eventType)
        if name == b"windows_generic_MSG":
            result = frame.handle_message(self, int(message))
            if result is not None:
                return True, result
        return super().nativeEvent(eventType, message)

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
        if self._backdrop is None:
            self._backdrop = Backdrop(self)
        dialog.open()
        return dialog

    def _on_dialog_finished(self, _result: int) -> None:
        self._dialog = None
        if self._backdrop is not None:
            self._backdrop.dismiss()
            self._backdrop = None

    def new_profile(self) -> None:
        if self.controller.ready:
            self.show_home()  # Ctrl+N also works from the settings page
            log.debug("New profile dialog opened")
            self._show_dialog(lambda: ProfileDialog(self.controller, parent=self))

    def edit_profile(self, name: str) -> None:
        if not self.controller.ready:
            return
        if self.controller.state_of(name) != CLOSED:
            log.warning("Tried to edit '%s' while it is open", name)
            self.toasts.show("warning", "Close the profile before editing it.")
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
        """File chooser requested by a page in WebKit (see launcher.py). The page is in the
        browser window the user is using, so the chooser is brought in front of it."""
        filters = ";;".join(f"{label} ({patterns})" for label, patterns in launcher.file_types(accept))
        tries = 0

        def raise_chooser() -> None:
            nonlocal tries
            tries += 1
            if frame.bring_dialog_to_front() or tries >= CHOOSER_RAISE_TRIES:
                raiser.stop()

        raiser = QTimer(self)
        raiser.timeout.connect(raise_chooser)
        raiser.start(CHOOSER_RAISE_MS)
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
        finally:
            raiser.stop()
            raiser.deleteLater()
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
        visible = self._logs_visible()
        self.logs_button.set_active(visible)
        self.logs_button.set_badge(0 if visible else self._unseen_problems)
        self.logs_button.setToolTip("Hide logs (Ctrl+L)" if visible else "Logs (Ctrl+L)")

    def _drain_logs(self) -> None:
        entries = self.log_handler.drain()
        if not entries:
            return
        # Before the window exists the entries stay in the handler; it shows them when created.
        if self.log_window is not None:
            self.log_window.append(entries)
        if not self._logs_visible():
            problems = sum(1 for entry in entries if entry.level >= logging.WARNING)
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
        self.controller.shutdown()  # disables the buttons through ready_changed

    def _finish_close(self) -> None:
        self._can_close = True
        self._drain_logs()
        self.close()
        QApplication.quit()
