"""Main window: title bar, then either the profile list or the settings page, and the status bar."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QEnterEvent,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import browser_session
import diagnostics
import launcher
import logs
import updater
from about import APP_NAME, REPOSITORY
from profiles import DATA_DIR, Devices, Profile, browser_label, ios_version
from settings import Settings, save_settings
from ui import frame, line_icons, theme
from ui.confirm_dialog import ConfirmDialog
from ui.controller import CLOSED, CLOSING, OPENED, OPENING, AppController
from ui.dialog import Backdrop
from ui.icons import IconBadge, IconButton, TerminalButton
from ui.log_window import LogWindow
from ui.page_dialog import PageDialog
from ui.profile_dialog import ENGINE_LABELS, MODE_LABELS, THEME_LABELS, ProfileDialog
from ui.release import ReleaseChecker, UpdateDownloader
from ui.service import DialogFuture, FilesFuture
from ui.settings_page import SettingsPage
from ui.sound import NotificationSound
from ui.title_bar import TitleBar
from ui.toast import ToastHost
from ui.widgets import ColorTag, ProfileCounter, StatusDot, hbox, make_button, make_label, restyle, set_variant

log = logging.getLogger("app.gui")

# Shown next to the name; a closed profile shows nothing (its grey dot says it).
STATE_LABELS = {CLOSED: "", OPENING: "Starting…", OPENED: "Running", CLOSING: "Saving session…"}
LOG_POLL_MS = 100  # how often new log entries are moved to the logs window
PAGE_MS = 200  # page transition
PAGE_SLIDE_PX = 14
UNDO_MS = 8000  # how long a deleted profile can be restored with "Undo"
OPENED_REFRESH_MS = 60_000  # "Opened 5 min ago" is kept up to date
PROFILE_MIME = "application/x-openprofiles-profile"  # a card being dragged
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
    "restored": ("Profile restored", "{n} profiles restored"),
    "file": ("Download saved to Downloads", "{n} downloads saved to Downloads"),
}


def opened_ago(iso: str, now: datetime | None = None) -> str:
    """ "Opened 5 min ago", "Opened yesterday"... from the time saved when it was last opened."""
    if not iso:
        return "Never opened"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    seconds = ((now or datetime.now()) - then).total_seconds()
    if seconds < 60:
        return "Opened just now"
    if seconds < 3600:
        return f"Opened {int(seconds // 60)} min ago"
    if seconds < 86_400:
        return f"Opened {int(seconds // 3600)} h ago"
    days = int(seconds // 86_400)
    if days == 1:
        return "Opened yesterday"
    if days < 7:
        return f"Opened {days} days ago"
    return f"Opened {then.day} {then.strftime('%b %Y')}"


def describe_profile(profile: Profile, devices: Devices) -> str:
    user_agent = profile.user_agent or devices.get(profile.device, {}).get("user_agent", "")
    if profile.mode == "desktop":
        parts = [MODE_LABELS["desktop"], browser_label(user_agent)]
    else:
        parts = [profile.device, ios_version(user_agent), ENGINE_LABELS[profile.engine]]
    return "  ·  ".join([*parts, THEME_LABELS[profile.theme]])


class DragHandle(QWidget):
    """The grip at the left of a card: press it and move the mouse to put the profile elsewhere."""

    def __init__(self, card: ProfileCard):
        super().__init__(card)
        self.card = card
        self.setFixedSize(18, 40)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to reorder")
        self._hover = False
        self._press_y: float | None = None
        self._dragging = False

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        active = self._hover or self._dragging
        color = theme.color("text") if active else theme.color("disabled")
        line_icons.paint(painter, "grip", QRectF(1, (self.height() - 16) / 2, 16, 16), color)

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_y = event.globalPosition().y()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press_y is None:
            return
        y = event.globalPosition().y()
        board = self.card.main.list_widget
        if not self._dragging and abs(y - self._press_y) >= QApplication.startDragDistance():
            self._dragging = board.begin_drag(self.card, self._press_y)
            self.update()
        if self._dragging and board.dragged is self.card:  # Esc may have cancelled it
            board.drag_to(y)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        board = self.card.main.list_widget
        if self._dragging and board.dragged is self.card:
            board.end_drag()
        self._press_y = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.update()
        super().mouseReleaseEvent(event)


class ProfileCard(QFrame):
    """One profile: its grip, label color, status, name and state, summary and notes, and its
    actions (open or close, edit, duplicate and delete)."""

    def __init__(self, window: MainWindow, profile: Profile):
        super().__init__()
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)  # the border lights up on hover
        self.main = window
        self.profile = profile
        controller = window.controller

        self.handle = DragHandle(self)
        self.dot = StatusDot()
        name = make_label(profile.name, name="profileName")
        self.state_label = make_label(name="stateLabel")
        self.details = make_label("", "muted")
        self.notes = make_label("", "small")
        title = QHBoxLayout()
        title.setSpacing(10)
        title.addWidget(name)
        title.addWidget(self.state_label)
        title.addStretch(1)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addLayout(title)
        texts.addWidget(self.details)
        texts.addWidget(self.notes)

        self.action = make_button("Open", "primary", width=92)
        self.action.clicked.connect(self._toggle)
        self.edit = IconButton("pencil", "Edit")
        self.edit.clicked.connect(lambda: window.edit_profile(profile.name))
        self.duplicate = IconButton("copy", "Duplicate (same settings, its own fingerprint)")
        self.duplicate.clicked.connect(lambda: controller.duplicate_profile(profile.name))
        self.delete = IconButton("trash", "Delete", danger=True)
        self.delete.clicked.connect(lambda: window.ask_delete(profile.name))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 12, 14, 12)
        layout.setSpacing(8)
        # The colored bar of the label; its place is kept without one, so every card lines up.
        self.tag = ColorTag(profile.color)
        layout.addWidget(self.handle)
        layout.addWidget(self.tag)
        layout.addSpacing(6)
        layout.addWidget(self.dot)
        layout.addSpacing(6)
        layout.addLayout(texts, 1)
        layout.addLayout(hbox(self.action, 6, self.edit, self.duplicate, self.delete, spacing=2))
        self.update_profile(profile)
        self.set_state(controller.state_of(profile.name))

    def update_profile(self, profile: Profile) -> None:
        """Shows the profile's latest data (same name), without rebuilding the card."""
        self.profile = profile
        self.notes.setText(profile.notes)
        self.notes.setToolTip(profile.notes)
        self.notes.setVisible(bool(profile.notes))
        self.tag.color = profile.color
        self.tag.update()
        self.update_opened()

    def update_opened(self) -> None:
        summary = describe_profile(self.profile, self.main.controller.devices)
        opened = opened_ago(self.profile.last_opened)
        self.details.setText(f"{summary}  ·  {opened}" if opened else summary)

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
        self.duplicate.setEnabled(ready)
        self.delete.setEnabled(state == CLOSED and not self.main.controller.closing)


class ProfileList(QWidget):
    """The column of cards. A card dragged by its grip follows the mouse; the others slide
    out of its way, leaving a slot where it will land; near the top or bottom edge the list
    scrolls; Esc puts it back. Dropping it saves the new order, without rebuilding the list."""

    SLIDE_MS = 150
    EDGE = 48  # distance to the edge of the visible list where it starts scrolling
    SCROLL_STEP = 12

    def __init__(self, window: MainWindow):
        super().__init__()
        self.main = window
        self.setObjectName("profileList")
        self.dragged: ProfileCard | None = None
        self._slot: QFrame | None = None
        self._origin = 0
        self._offset = 0.0
        self._last_y = 0.0
        self._animations: dict[QWidget, QPropertyAnimation] = {}
        self._scroller = QTimer(self)
        self._scroller.setInterval(16)
        self._scroller.timeout.connect(self._auto_scroll)

    @property
    def _layout(self) -> QVBoxLayout:
        return self.main.list_layout

    def ordered_cards(self) -> list[ProfileCard]:
        """The cards in the order they are shown (the dragged one in its slot)."""
        result: list[ProfileCard] = []
        for index in range(self._layout.count()):
            item = self._layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, ProfileCard):
                result.append(widget)
            elif widget is not None and widget is self._slot and self.dragged is not None:
                result.append(self.dragged)
        return result

    def begin_drag(self, card: ProfileCard, press_y: float) -> bool:
        if self.dragged is not None or len(self.main.cards) < 2 or self.main.search.text().strip():
            return False
        self._origin = self._layout.indexOf(card)
        geometry = card.geometry()
        self._slot = QFrame(self)
        self._slot.setObjectName("dropSlot")
        self._slot.setFixedHeight(card.height())
        self._layout.insertWidget(self._origin, self._slot)
        self._layout.removeWidget(card)
        card.setGeometry(geometry)  # it stays where it was, now free of the layout
        card.raise_()
        card.setProperty("dragging", True)
        restyle(card)
        self._offset = press_y - card.mapToGlobal(QPoint(0, 0)).y()
        self.dragged = card
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)  # Esc cancels
        self._scroller.start()
        return True

    def drag_to(self, global_y: float) -> None:
        card = self.dragged
        if card is None:
            return
        self._last_y = global_y
        wanted = self.mapFromGlobal(QPoint(0, round(global_y - self._offset))).y()
        card.move(card.x(), max(0, min(wanted, self.height() - card.height())))  # drawn inside the list
        # Where it goes follows the mouse, even past the ends (so the first and last places
        # can always be reached).
        self._place_slot(wanted + card.height() / 2)

    def _place_slot(self, center: float) -> None:
        slot = self._slot
        if slot is None:
            return
        others = [c for c in self.ordered_cards() if c is not self.dragged]
        # Where the layout puts each card (not where an animation is taking it right now).
        index = 0
        for card in others:
            item = self._layout.itemAt(self._layout.indexOf(card))
            if item is not None and QRectF(item.geometry()).center().y() < center:
                index += 1
        if index == self._layout.indexOf(slot):
            return
        before = {c: c.pos() for c in others}
        self._layout.removeWidget(slot)
        self._layout.insertWidget(index, slot)
        self._layout.activate()
        for c in others:
            if c.pos() != before[c]:
                self._slide(c, before[c], c.pos())
        if self.dragged is not None:
            self.dragged.raise_()

    def _slide(self, widget: QWidget, start: QPoint, end: QPoint) -> None:
        previous = self._animations.pop(widget, None)
        if previous is not None:
            previous.stop()
        animation = QPropertyAnimation(widget, b"pos", self)
        animation.setDuration(self.SLIDE_MS)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.finished.connect(lambda: self._animations.pop(widget, None))
        self._animations[widget] = animation
        animation.start()

    def end_drag(self, *, cancel: bool = False) -> None:
        card, slot = self.dragged, self._slot
        if card is None or slot is None:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._scroller.stop()
        index = self._origin if cancel else self._layout.indexOf(slot)
        start = card.pos()
        self._layout.removeWidget(slot)
        slot.deleteLater()
        self._slot = None
        self.dragged = None
        self._layout.insertWidget(index, card)
        self._layout.activate()
        self._slide(card, start, card.pos())  # it glides into its place
        card.setProperty("dragging", False)
        restyle(card)
        if not cancel:
            self.main.save_order()

    def _auto_scroll(self) -> None:
        if self.dragged is None:
            return
        bar = self.main.list_scroll.verticalScrollBar()
        viewport = self.main.list_scroll.viewport()
        y = viewport.mapFromGlobal(QPoint(0, round(self._last_y))).y()
        step = 0
        if y < self.EDGE:
            step = -self.SCROLL_STEP
        elif y > viewport.height() - self.EDGE:
            step = self.SCROLL_STEP
        if step and bar.minimum() <= bar.value() + step <= bar.maximum():
            bar.setValue(bar.value() + step)
            self.drag_to(self._last_y)  # the list moved under the mouse

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            self.dragged is not None
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_Escape
        ):
            self.end_drag(cancel=True)
            return True
        return False


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
        self._restore_geometry()
        theme.on_theme_changed(self._on_theme_changed)
        self._on_theme_changed(theme.mode())

        controller.profiles_changed.connect(self.refresh)
        controller.ready_changed.connect(self._on_ready_changed)
        controller.state_changed.connect(self._on_state_changed)
        controller.message.connect(self.show_message)
        controller.notified.connect(self._on_notified)
        controller.files_requested.connect(self._choose_files)
        controller.dialog_requested.connect(self._show_page_dialog)
        controller.backup_finished.connect(self._on_backup_finished)
        controller.set_restore_tabs(settings.restore_tabs)
        controller.retry_available.connect(self.retry_button.setVisible)
        controller.shutdown_finished.connect(self._finish_close)
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.new_profile)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(self.toggle_settings)
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(self.toggle_logs)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.focus_search)
        self._opened_timer = QTimer(self)
        self._opened_timer.timeout.connect(self._update_opened)
        self._opened_timer.start(OPENED_REFRESH_MS)

        self._log_timer = QTimer(self)
        self._log_timer.timeout.connect(self._drain_logs)
        self._log_timer.start(LOG_POLL_MS)
        self.refresh()

        self.release_checker = ReleaseChecker(self)
        self.release_checker.found.connect(self._on_release_found)
        self.release_checker.failed.connect(self.title_bar.release_unavailable)
        self.release_checker.failed.connect(self.settings_page.release_unavailable)
        self.release_checker.check()
        self.update_downloader = UpdateDownloader(self)
        self.update_downloader.progress.connect(self._on_update_progress)
        self.update_downloader.finished.connect(self._on_update_downloaded)
        self.update_downloader.failed.connect(self._on_update_failed)

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
        self.search = QLineEdit()
        self.search.setObjectName("profileSearch")
        self.search.setPlaceholderText("Search")
        self.search.setToolTip("Search by name, notes or device (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(self._apply_filter)
        self._search_icon = self.search.addAction(QIcon(), QLineEdit.ActionPosition.LeadingPosition)
        home.addLayout(
            hbox(
                heading,
                4,
                self.counter,
                None,
                self.search,
                8,
                self.close_all_button,
                self.open_all_button,
                self.new_button,
            )
        )
        home.addSpacing(14)

        self.list_widget = ProfileList(self)
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_scroll.setWidget(self.list_widget)
        home.addWidget(self.list_scroll, 1)

        # Settings page, in the same place.
        self.settings_page = SettingsPage(self.settings.mode, self.settings.sounds, self.settings.restore_tabs)
        self.settings_page.back_requested.connect(self.show_home)
        self.settings_page.mode_changed.connect(self._on_mode_changed)
        self.settings_page.sounds_changed.connect(self._on_sounds_changed)
        self.settings_page.restore_tabs_changed.connect(self._on_restore_tabs_changed)
        self.settings_page.downloads_requested.connect(self.open_downloads_folder)
        self.settings_page.export_requested.connect(self.export_profiles)
        self.settings_page.update_requested.connect(self.ask_update)
        self.settings_page.import_requested.connect(self.import_profiles)
        self.settings_page.data_folder_requested.connect(self.open_data_folder)
        self.settings_page.releases_requested.connect(self.title_bar.open_releases)
        self.settings_page.repository_requested.connect(
            lambda: QDesktopServices.openUrl(QUrl(f"https://github.com/{REPOSITORY}"))
        )
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
        """Shows the profiles. If they are the same ones, in the same order, each card is only
        updated (no flicker, and a card being dragged is not disturbed); otherwise the list is
        built again."""
        profiles = self.controller.profiles()
        if self.cards and list(profiles) == list(self.cards):
            for name, profile in profiles.items():
                self.cards[name].update_profile(profile)
            self._apply_filter()
            self._update_toolbar()
            return
        self.list_widget.end_drag(cancel=True)
        self._rebuild(profiles)

    def _rebuild(self, profiles: dict[str, Profile]) -> None:
        while (item := self.list_layout.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        self.cards.clear()
        if profiles:
            for profile in profiles.values():
                card = ProfileCard(self, profile)
                self.cards[profile.name] = card
                self.list_layout.addWidget(card)
        else:
            self.list_layout.addWidget(self._empty_state())
        self.no_match = make_label("No profiles match the search.", "muted")
        self.no_match.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.list_layout.addSpacing(4)
        self.list_layout.addWidget(self.no_match)
        self.list_layout.addStretch(1)
        self._apply_filter()
        self._update_toolbar()

    # --- search, reorder and time since opened ------------------------------
    def focus_search(self) -> None:
        self.show_home()
        self.search.setFocus()
        self.search.selectAll()

    def _apply_filter(self) -> None:
        """Shows only the profiles whose name, notes or summary (mode, device, browser...) contain
        the search: what their cards show, plus the color of their label."""
        query = self.search.text().strip().lower()
        shown = 0
        for card in self.cards.values():
            profile = card.profile
            summary = describe_profile(profile, self.controller.devices)
            haystack = " ".join((profile.name, profile.notes, summary, profile.color)).lower()
            visible = not query or query in haystack
            card.setVisible(visible)
            card.handle.setVisible(not query)  # the order is changed with the whole list in view
            shown += visible
        self.no_match.setVisible(bool(self.cards) and shown == 0)

    def save_order(self) -> None:
        """Keeps the order the cards were dragged into."""
        names = [card.profile.name for card in self.list_widget.ordered_cards()]
        if names != list(self.cards):
            self.cards = {name: self.cards[name] for name in names}
            self.controller.reorder(names)

    def move_profile(self, name: str, before: str | None) -> None:
        """Puts `name` right before `before` (or last) and saves the order."""
        names = [n for n in self.cards if n != name]
        if name not in self.cards:
            return
        names.insert(names.index(before) if before in names else len(names), name)
        if names != list(self.cards):
            self.controller.reorder(names)
            self.refresh()

    def _update_opened(self) -> None:
        for card in self.cards.values():
            card.update_opened()

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

    def _on_notified(self, kind: str, name: str, detail: str) -> None:
        """Shows a notification for an event; the name of the profile is never shown."""
        self.sound.play_for(kind)  # profiles created, edited or deleted, and browsers downloaded
        if kind == "deleted":  # it can be brought back for a few seconds
            toast = self.toasts.show(
                kind,
                "Profile deleted",
                detail,
                action=("Undo", lambda: self.controller.undo_delete(name)),
                duration=UNDO_MS,
            )

            def finished(_toast: object) -> None:  # "Undo" was not pressed: deleted for good
                self.controller.finish_delete(name)

            toast.finished.connect(finished)
            return
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

    def _on_restore_tabs_changed(self, enabled: bool) -> None:
        self.controller.set_restore_tabs(enabled)
        self.settings.restore_tabs = enabled
        log.info("Reopen the last tabs: %s", "on" if enabled else "off")
        self._save_settings()

    def _save_settings(self) -> None:
        try:
            save_settings(self.settings)
        except OSError as e:
            log.warning("Could not save the settings: %s", e)
            self.toasts.show("warning", "Could not save the settings", str(e))

    def _on_theme_changed(self, _theme: str) -> None:
        frame.set_border_color(self, theme.color("border"))
        self._search_icon.setIcon(QIcon(line_icons.pixmap("search", theme.color("muted"), 14)))

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
        message = (
            f'"{name}" will be deleted for good together with its session and data. You can undo it for a few seconds.'
        )
        dialog = self._show_dialog(lambda: ConfirmDialog("Delete profile", message, "Delete", parent=self))
        if dialog is not None:
            dialog.accepted.connect(lambda: self.controller.delete_profile(name))

    def _show_page_dialog(self, kind: str, message: str, default: str, site: str, future: DialogFuture) -> None:
        """A page's alert or question, in front of the browser; the answer goes back to the page."""
        dialog = PageDialog(kind, message, default, site)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        def finished(_result: int) -> None:
            if not future.done():
                future.set_result(dialog.answer())

        dialog.finished.connect(finished)
        dialog.show_in_front()

    # --- updating ------------------------------------------------------------
    def ask_update(self, tag: str) -> None:
        if self.update_downloader.running:
            return
        message = (
            f"OpenProfiles will download {tag}, close the profiles (saving their sessions), "
            "replace itself and open again."
        )
        dialog = self._show_dialog(
            lambda: ConfirmDialog(f"Update to {tag}", message, "Update", parent=self, variant="primary")
        )
        if dialog is not None:
            dialog.accepted.connect(self._start_update)

    def _start_update(self) -> None:
        log.info("Downloading the update")
        self.status.setText("Downloading the update…")
        self.update_downloader.start()

    def _on_update_progress(self, done: int, total: int) -> None:
        if total:
            self.status.setText(f"Downloading the update… {done * 100 // total} %")

    def _on_update_downloaded(self, path: str) -> None:
        self.status.setText("Installing the update…")
        try:
            updater.install_update(Path(path))
        except OSError as e:
            self._on_update_failed(f"Could not start the update: {e}")
            return
        self.quit_app()  # the profiles close and save their sessions; then the new version opens

    def _on_update_failed(self, error: str) -> None:
        self.status.setText("")
        self.toasts.show("error", "Could not update", error)

    def export_profiles(self) -> None:
        if self.controller.states:
            self.toasts.show("warning", "Close the profiles before exporting them.")
            return
        default = str(browser_session.downloads_folder() / f"OpenProfiles-{datetime.now():%Y%m%d-%H%M}.zip")
        path, _filter = QFileDialog.getSaveFileName(self, "Export profiles", default, "OpenProfiles backup (*.zip)")
        if path:
            self.controller.export_profiles(path)

    def import_profiles(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Import profiles", str(browser_session.downloads_folder()), "OpenProfiles backup (*.zip)"
        )
        if path:
            self.controller.import_profiles(path)

    def _on_backup_finished(self, kind: str, title: str, detail: str) -> None:
        self.status.setText("")
        self.toasts.show(kind, title, detail)
        if kind == "created":
            self.refresh()

    def open_downloads_folder(self) -> None:
        folder = browser_session.downloads_folder()
        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            log.info("Opened the Downloads folder: %s", folder)
        else:
            log.error("Could not open the Downloads folder: %s", folder)
            self.toasts.show("error", "Could not open the Downloads folder", str(folder))

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
            self.log_window = LogWindow(self.log_handler, lambda: diagnostics.report(self.settings, self.log_handler))
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
        self._save_geometry()
        self.controller.shutdown()  # disables the buttons through ready_changed

    def quit_app(self) -> None:
        """Closes the app (after an update is downloaded, for example)."""
        self.close()

    # --- window place -------------------------------------------------------
    def _restore_geometry(self) -> None:
        if self.settings.window:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window.encode("ascii")))

    def _save_geometry(self) -> None:
        geometry = bytes(self.saveGeometry().toBase64().data()).decode("ascii")
        if geometry != self.settings.window:
            self.settings.window = geometry
            self._save_settings()

    def _finish_close(self) -> None:
        self._can_close = True
        self._drain_logs()
        self.close()
        QApplication.quit()
