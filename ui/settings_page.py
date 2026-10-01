"""
Settings page, shown inside the main window in place of the profile list. A side
bar switches between its tabs:

- General: appearance mode, notification sounds and the data folder.
- Browsers: reopening the last tabs, where downloads go, and the protections in place.
- Backup: exporting and importing profiles.
- About: the installed version, a newer release (and updating to it), and the project.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QEnterEvent, QFont, QKeyEvent, QKeySequence, QPainter, QPaintEvent, QShortcut
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import updater
from about import APP_NAME, APP_VERSION, is_newer
from ui import line_icons, theme
from ui.icons import IconButton, ToggleSwitch
from ui.widgets import SegmentedControl, hbox, make_button, make_label, restyle

MODE_LABELS = {"auto": "Auto", "light": "Light", "dark": "Dark"}
# Tab: (icon, title, description shown under the title)
TABS = {
    "General": ("sliders", "General", "Appearance, sounds and where the app keeps its data."),
    "Browsers": ("globe", "Browsers", "How the profiles' browsers behave."),
    "Backup": ("archive", "Backup", "Copy your profiles to a file, or bring them back from one."),
    "About": ("info", "About", "Version and updates."),
}
NAV_WIDTH = 188
CONTENT_MAX_WIDTH = 620
SLIDE_PX = 10
SLIDE_MS = 160


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("settingsDivider")
    return line


def _group(*rows: QBoxLayout) -> QFrame:
    """A rounded card with the rows separated by thin lines."""
    group = QFrame()
    group.setObjectName("settingsGroup")
    layout = QVBoxLayout(group)
    layout.setContentsMargins(18, 4, 18, 4)
    layout.setSpacing(0)
    for index, row in enumerate(rows):
        if index:
            layout.addWidget(_divider())
        layout.addLayout(row)
    return group


def _row(name: str, description: str | QLabel, control: QWidget) -> QHBoxLayout:
    """A setting: its name and description on the left, its control on the right."""
    texts = QVBoxLayout()
    texts.setSpacing(2)
    texts.addWidget(make_label(name, name="settingName"))
    texts.addWidget(make_label(description, "small", wrap=True) if isinstance(description, str) else description)
    row = QHBoxLayout()
    row.setContentsMargins(0, 14, 0, 14)
    row.setSpacing(16)
    row.addLayout(texts, 1)
    row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


class NavItem(QAbstractButton):
    """A tab of the side bar: its icon and name; the selected one sits on a soft background."""

    HEIGHT = 36

    def __init__(self, icon: str, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.icon_name = icon
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(self.HEIGHT)
        self._hover = False

    def sizeHint(self) -> QSize:
        return QSize(NAV_WIDTH, self.HEIGHT)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            event.ignore()  # the page moves between the tabs
            return
        super().keyPressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self.isChecked() or self._hover:
            background = theme.color("surface_hover")
            if not self.isChecked():
                background.setAlphaF(0.6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(rect, 8, 8)
        active = self.isChecked() or self._hover
        color = theme.color("text") if active else theme.color("muted")
        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.color("accent"))
            painter.drawRoundedRect(QRectF(0, 9, 3, rect.height() - 18), 1.5, 1.5)
        line_icons.paint(painter, self.icon_name, QRectF(14, (rect.height() - 16) / 2, 16, 16), color)
        font = QFont(self.font())
        font.setPixelSize(14)
        font.setWeight(QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(rect.adjusted(40, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, self.text())


class SettingsPage(QWidget):
    back_requested = Signal()
    mode_changed = Signal(str)  # auto, light or dark
    sounds_changed = Signal(bool)
    restore_tabs_changed = Signal(bool)
    downloads_requested = Signal()
    export_requested = Signal()
    import_requested = Signal()
    data_folder_requested = Signal()
    releases_requested = Signal()
    repository_requested = Signal()
    update_requested = Signal(str)  # tag of the new version

    def __init__(self, mode: str, sounds: bool, restore_tabs: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("page")
        self._build_controls(mode, sounds, restore_tabs)

        self.back_button = IconButton("chevron-left", "Back (Esc)", size=(34, 34), icon_size=18)
        self.back_button.clicked.connect(self.back_requested)
        QShortcut(
            QKeySequence(Qt.Key.Key_Escape), self, context=Qt.ShortcutContext.WidgetWithChildrenShortcut
        ).activated.connect(self.back_requested)
        header = hbox(self.back_button, 2, make_label("Settings", name="pageTitle"), None, spacing=8)

        # Side bar with the tabs, and the page of the selected one.
        self.nav_items: dict[str, NavItem] = {}
        self._nav_group = QButtonGroup(self)
        nav = QVBoxLayout()
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(4)
        self.stack = QStackedWidget()
        pages = {
            "General": self._general(),
            "Browsers": self._browsers(),
            "Backup": self._backup(),
            "About": self._about(),
        }
        for key, (icon, _title, _description) in TABS.items():
            item = NavItem(icon, key)
            item.clicked.connect(lambda _checked=False, tab=key: self.show_tab(tab))
            self._nav_group.addButton(item)
            self.nav_items[key] = item
            nav.addWidget(item)
            self.stack.addWidget(self._page(key, pages[key]))
        nav.addStretch(1)
        side = QWidget()
        side.setFixedWidth(NAV_WIDTH)
        side.setLayout(nav)

        content = QWidget()
        content.setMaximumWidth(CONTENT_MAX_WIDTH)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.addWidget(self.stack)
        body = QHBoxLayout()
        body.setSpacing(28)
        body.addWidget(side)
        body.addWidget(content, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addSpacing(18)
        layout.addLayout(body, 1)
        self._slide = QPropertyAnimation(self)
        self.show_tab("General", animate=False)

    # --- controls -----------------------------------------------------------
    def _build_controls(self, mode: str, sounds: bool, restore_tabs: bool) -> None:
        self.mode = SegmentedControl(list(MODE_LABELS.values()), small=True, expand=True)
        self.mode.setFixedWidth(228)
        self.mode.set_value(MODE_LABELS.get(mode, "Auto"))
        self.mode.changed.connect(self._on_mode_clicked)
        self.sounds = ToggleSwitch(checked=sounds)
        self.sounds.setToolTip("Notification sounds")
        self.sounds.toggled.connect(self.sounds_changed)
        self.data_button = make_button("Open folder", small=True)
        self.data_button.clicked.connect(self.data_folder_requested)
        self.restore_tabs = ToggleSwitch(checked=restore_tabs)
        self.restore_tabs.setToolTip("Reopen the last tabs")
        self.restore_tabs.toggled.connect(self.restore_tabs_changed)
        self.downloads_button = make_button("Open folder", small=True)
        self.downloads_button.clicked.connect(self.downloads_requested)
        self.export_button = make_button("Export…", small=True)
        self.export_button.clicked.connect(self.export_requested)
        self.import_button = make_button("Import…", small=True)
        self.import_button.clicked.connect(self.import_requested)
        self.releases_button = make_button("View releases", small=True)
        self.releases_button.clicked.connect(self.releases_requested)
        self.update_button = make_button("Update", "primary", small=True)
        self.update_button.clicked.connect(lambda: self.update_requested.emit(self.latest_tag))
        self.update_button.hide()
        self.repository_button = make_button("Open on GitHub", small=True)
        self.repository_button.clicked.connect(self.repository_requested)
        self.latest_tag = ""
        self.version_status = make_label("Checking for updates…", "small", wrap=True)

    def _general(self) -> list[QFrame]:
        return [
            _group(
                _row("Mode", "Auto follows the Windows theme.", self.mode),
                _row(
                    "Notification sounds",
                    "A short chime when a profile is created, edited or deleted, "
                    "and when the browsers finish downloading.",
                    self.sounds,
                ),
                _row("Data folder", "Profiles, sessions, browsers and logs.", self.data_button),
            )
        ]

    def _browsers(self) -> list[QFrame]:
        return [
            _group(
                _row(
                    "Reopen the last tabs",
                    "Each profile opens the tabs it had open the last time, instead of its start page.",
                    self.restore_tabs,
                ),
                _row(
                    "Downloads",
                    "Files downloaded in the profiles are saved to your Downloads folder.",
                    self.downloads_button,
                ),
            ),
            _group(
                _row(
                    "Protection",
                    "No automation marks, a fingerprint of its own for each profile, pop-ups blocked and "
                    "logins kept between sessions (encrypted with your Windows account).",
                    make_label("Always on", "small"),
                ),
            ),
        ]

    def _backup(self) -> list[QFrame]:
        return [
            _group(
                _row(
                    "Export profiles",
                    "Saves every profile, with its settings, fingerprint and session, to a .zip file.",
                    self.export_button,
                ),
                _row(
                    "Import profiles",
                    "Adds the profiles of a backup. Sessions are encrypted with your Windows account: on "
                    "another computer or user they keep their settings, and you sign in again.",
                    self.import_button,
                ),
            )
        ]

    def _about(self) -> list[QFrame]:
        version_buttons = QWidget()
        version_buttons.setLayout(hbox(self.update_button, self.releases_button, spacing=8))
        return [
            _group(
                _row(f"{APP_NAME} v{APP_VERSION}", self.version_status, version_buttons),
                _row("Source code", "OpenProfiles is open source, on GitHub.", self.repository_button),
            )
        ]

    def _page(self, key: str, groups: list[QFrame]) -> QScrollArea:
        """A tab: its title and description, then its groups of settings."""
        _icon, title, description = TABS[key]
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 8, 12)
        layout.setSpacing(0)
        layout.addWidget(make_label(title, name="tabTitle"))
        layout.addSpacing(2)
        layout.addWidget(make_label(description, "small"))
        layout.addSpacing(16)
        for index, group in enumerate(groups):
            if index:
                layout.addSpacing(12)
            layout.addWidget(group)
        layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    # --- tabs ---------------------------------------------------------------
    def current_tab(self) -> str:
        return next(key for key, item in self.nav_items.items() if item.isChecked())

    def show_tab(self, key: str, animate: bool = True) -> None:
        index = list(TABS).index(key)
        self.nav_items[key].setChecked(True)
        if self.stack.currentIndex() == index:
            return
        self._slide.stop()
        self.stack.setCurrentIndex(index)
        page = self.stack.currentWidget()
        if animate and self.isVisible() and page is not None:
            # The new tab rises a little into place (no opacity effect: the page may have one).
            self._slide = QPropertyAnimation(page, b"pos", self)
            self._slide.setDuration(SLIDE_MS)
            self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._slide.setStartValue(QPoint(0, SLIDE_PX))
            self._slide.setEndValue(QPoint(0, 0))
            self._slide.start()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Up and down move between the tabs (when a tab of the side bar has the focus)."""
        keys = list(TABS)
        index = keys.index(self.current_tab())
        if event.key() == Qt.Key.Key_Up and index > 0:
            self.show_tab(keys[index - 1])
            self.nav_items[keys[index - 1]].setFocus()
        elif event.key() == Qt.Key.Key_Down and index < len(keys) - 1:
            self.show_tab(keys[index + 1])
            self.nav_items[keys[index + 1]].setFocus()
        else:
            super().keyPressEvent(event)

    # --- values -------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        """Shows `mode` as selected, without emitting mode_changed."""
        self.mode.set_value(MODE_LABELS[mode])

    def _on_mode_clicked(self, label: str) -> None:
        mode = next(key for key, value in MODE_LABELS.items() if value == label)
        self.mode_changed.emit(mode)

    def set_release(self, tag: str) -> None:
        self.latest_tag = tag
        newer = is_newer(tag)
        if newer:
            self._set_version_status(f"{tag} is available.", update=not updater.can_update())
        else:
            self._set_version_status("You have the latest version.")
        # The .exe updates itself; from the source code the button would not apply.
        self.update_button.setText(f"Update to {tag}")
        self.update_button.setVisible(newer and updater.can_update())
        self.nav_items["About"].setToolTip(f"{tag} is available" if newer else "")

    def release_unavailable(self, _error: str = "") -> None:
        self._set_version_status("Could not check for updates.")

    def _set_version_status(self, text: str, update: bool = False) -> None:
        """With an update available, "View releases" becomes the yellow primary button."""
        self.version_status.setText(text)
        self.releases_button.setProperty("variant", "primary" if update else "secondary")
        restyle(self.releases_button)
