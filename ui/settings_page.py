"""
Settings page, shown inside the main window in place of the profile list.

General: appearance mode, notification sounds and the data folder.
About: the installed version, whether there is a newer release, and a link to them.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from about import APP_NAME, APP_VERSION, is_newer
from ui.icons import IconButton, ToggleSwitch
from ui.widgets import SegmentedControl, make_button, make_label, restyle

MODE_LABELS = {"auto": "Auto", "light": "Light", "dark": "Dark"}
COLUMN_WIDTH = 680  # the settings column stays readable on wide windows


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


class SettingsPage(QWidget):
    back_requested = Signal()
    mode_changed = Signal(str)  # auto, light or dark
    sounds_changed = Signal(bool)
    data_folder_requested = Signal()
    releases_requested = Signal()

    def __init__(self, mode: str, sounds: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("page")

        self.back_button = IconButton("chevron-left", "Back (Esc)", size=(34, 34), icon_size=18)
        self.back_button.clicked.connect(self.back_requested)
        QShortcut(
            QKeySequence(Qt.Key.Key_Escape), self, context=Qt.ShortcutContext.WidgetWithChildrenShortcut
        ).activated.connect(self.back_requested)

        self.mode = SegmentedControl(list(MODE_LABELS.values()), small=True, expand=True)
        self.mode.setFixedWidth(228)
        self.mode.set_value(MODE_LABELS.get(mode, "Auto"))
        self.mode.changed.connect(self._on_mode_clicked)
        self.sounds = ToggleSwitch(checked=sounds)
        self.sounds.setToolTip("Notification sounds")
        self.sounds.toggled.connect(self.sounds_changed)
        self.data_button = make_button("Open folder", small=True)
        self.data_button.clicked.connect(self.data_folder_requested)
        self.releases_button = make_button("View releases", small=True)
        self.releases_button.clicked.connect(self.releases_requested)
        self.version_status = make_label("Checking for updates…", "small", wrap=True)
        version_row = _row(f"{APP_NAME} v{APP_VERSION}", self.version_status, self.releases_button)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self.back_button)
        header.addWidget(make_label("Settings", name="pageTitle"))
        header.addStretch(1)
        column.addLayout(header)
        column.addSpacing(22)
        column.addWidget(make_label("General", name="sectionTitle"))
        column.addSpacing(8)
        column.addWidget(
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
        )
        column.addSpacing(24)
        column.addWidget(make_label("About", name="sectionTitle"))
        column.addSpacing(8)
        column.addWidget(_group(version_row))
        column.addStretch(1)

        body = QWidget()
        body.setObjectName("page")
        centered = QHBoxLayout(body)
        centered.setContentsMargins(0, 0, 8, 12)
        centered.addStretch(1)
        holder = QWidget()
        holder.setMaximumWidth(COLUMN_WIDTH)
        holder.setLayout(column)
        centered.addWidget(holder, 100)
        centered.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

    def set_mode(self, mode: str) -> None:
        """Shows `mode` as selected, without emitting mode_changed."""
        self.mode.set_value(MODE_LABELS[mode])

    def _on_mode_clicked(self, label: str) -> None:
        mode = next(key for key, value in MODE_LABELS.items() if value == label)
        self.mode_changed.emit(mode)

    def set_release(self, tag: str) -> None:
        if is_newer(tag):
            self._set_version_status(f"{tag} is available.", update=True)
        else:
            self._set_version_status("You have the latest version.")

    def release_unavailable(self, _error: str = "") -> None:
        self._set_version_status("Could not check for updates.")

    def _set_version_status(self, text: str, update: bool = False) -> None:
        """With an update available, "View releases" becomes the yellow primary button."""
        self.version_status.setText(text)
        self.releases_button.setProperty("variant", "primary" if update else "secondary")
        restyle(self.releases_button)
