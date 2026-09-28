"""Dialog to create a profile or edit every setting of an existing one."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QComboBox, QDialog, QGridLayout, QLayout, QLineEdit, QVBoxLayout, QWidget

from profiles import (
    IPHONE_MODELS,
    START_URL,
    Profile,
    ProfileError,
    desktop_user_agent,
    iphone_user_agent,
    load_profiles,
    pick_device,
)
from ui.controller import AppController
from ui.widgets import SegmentedControl, hbox, make_button, make_label

RANDOM = "Random"
MODE_LABELS = {"desktop": "Desktop", "iphone": "iPhone"}  # also the order shown in the dialog
ENGINE_LABELS = {"webkit": "WebKit", "chromium": "Chromium"}
THEME_LABELS = {"dark": "Dark", "light": "Light"}
QUALITY_LABELS = {"smooth": "Smooth", "sharp": "Sharp"}
LOCALES = "es-ES es-MX es-AR es-CO es-CL es-PE es-US en-US en-GB pt-BR pt-PT fr-FR de-DE it-IT".split()
TIMEZONES = """
    Europe/Madrid Atlantic/Canary Europe/Lisbon Europe/London Europe/Paris Europe/Berlin Europe/Rome
    America/New_York America/Chicago America/Denver America/Los_Angeles America/Mexico_City America/Bogota
    America/Lima America/Caracas America/Santiago America/Argentina/Buenos_Aires America/Sao_Paulo
    Asia/Dubai Asia/Tokyo Australia/Sydney UTC
""".split()


def key_for(labels: dict[str, str], label: str) -> str:
    """Internal value from its visible label ('Sharp' -> 'sharp')."""
    return next(k for k, v in labels.items() if v == label)


def with_current(options: Sequence[str], current: str) -> list[str]:
    """Adds the current value to the list when missing (e.g. a hand-edited language)."""
    return list(options) if current in options else [current, *options]


def combo(options: Sequence[str], current: str) -> QComboBox:
    box = QComboBox()
    box.addItems(list(options))
    box.setCurrentText(current)
    box.setMaxVisibleItems(14)
    return box


class ProfileDialog(QDialog):
    """Creates a profile (profile=None) or edits one.

    In "Desktop" mode the engine is always Chromium, so the iPhone model, engine
    and quality are hidden. Quality is only shown with WebKit.
    """

    def __init__(self, controller: AppController, profile: Profile | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.devices = controller.iphones
        self.original = profile
        self.saved: Profile | None = None
        editing = profile is not None
        # Last user-agent filled in automatically: unless the user edited it by
        # hand, it is regenerated when the mode or model changes.
        self._auto_user_agent = profile.user_agent if profile else ""
        self.setWindowTitle("Edit profile" if editing else "New profile")

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 26)
        root.setSpacing(0)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)  # the dialog always fits its content
        root.addWidget(make_label("Edit profile" if editing else "New profile", name="dialogTitle"))
        root.addSpacing(4)
        subtitle = (
            "Changes apply the next time you open the profile."
            if editing
            else "Each profile keeps its own session, isolated from the rest."
        )
        root.addWidget(make_label(subtitle, "muted"))
        root.addSpacing(16)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setColumnMinimumWidth(0, 282)
        grid.setColumnMinimumWidth(1, 282)
        root.addLayout(grid)
        self._grid = grid

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("account_1")
        self._add_field("Name", self.name_edit, row=0, span=2)

        self.mode = SegmentedControl(list(MODE_LABELS.values()))
        self.mode.changed.connect(self._on_mode_change)
        self._add_field("Mode", self.mode, row=2, span=2)

        # iPhone mode only
        models = list(reversed(list(self.devices)))
        self.device_box = combo(models if editing else [RANDOM, *models], RANDOM)
        self.engine = SegmentedControl(list(ENGINE_LABELS.values()))
        self.engine.changed.connect(self._on_engine_change)
        self.quality = SegmentedControl(list(QUALITY_LABELS.values()))
        self._iphone_fields = [
            *self._add_field("iPhone", self.device_box, row=4, span=2),
            *self._add_field("Engine", self.engine, row=6),
        ]
        self._quality_fields = self._add_field("WebKit quality", self.quality, row=6, column=1)

        self.browser_theme = SegmentedControl(list(THEME_LABELS.values()))
        self._add_field("Browser theme", self.browser_theme, row=8)
        locale = profile.locale if profile else "es-ES"
        self.locale_box = combo(with_current(LOCALES, locale), locale)
        self._add_field("Language", self.locale_box, row=8, column=1)

        timezone = profile.timezone if profile else "Europe/Madrid"
        self.timezone_box = combo(with_current(TIMEZONES, timezone), timezone)
        self._add_field("Time zone", self.timezone_box, row=10)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(START_URL)
        self._add_field("Start URL", self.url_edit, row=10, column=1)

        self.ua_edit = QLineEdit()
        self.ua_edit.setPlaceholderText("Leave empty to generate a random one on save")
        random_button = make_button("Random", width=104)
        random_button.clicked.connect(self._randomize_user_agent)
        ua_row = QWidget()
        ua_row.setLayout(hbox(self.ua_edit, random_button))
        self._add_field("User-agent", ua_row, row=12, span=2)

        self.hint = make_label(role="small", wrap=True)
        self.error = make_label(role="error", wrap=True)
        self.error.hide()
        root.addWidget(self.hint)
        root.addSpacing(4)
        root.addWidget(self.error)
        root.addSpacing(18)

        cancel = make_button("Cancel", width=110)
        cancel.clicked.connect(self.reject)
        save = make_button("Save" if editing else "Create", "primary", width=110)
        save.clicked.connect(self._save)
        save.setDefault(True)  # Enter saves
        root.addLayout(hbox(None, cancel, save))

        # Initial values
        if profile:
            self.name_edit.setText(profile.name)
            self.mode.set_value(MODE_LABELS[profile.mode])
            self.device_box.setCurrentText(profile.device if profile.device in self.devices else models[0])
            # The chosen engine only applies to iPhone mode (the desktop mode is Chromium).
            self.engine.set_value(ENGINE_LABELS[profile.engine if profile.mode == "iphone" else "webkit"])
            self.quality.set_value(QUALITY_LABELS[profile.quality])
            self.browser_theme.set_value(THEME_LABELS[profile.theme])
            self.url_edit.setText(profile.start_url)
            self.ua_edit.setText(profile.user_agent)
            self.ua_edit.setCursorPosition(0)
        else:
            self.mode.set_value(MODE_LABELS["desktop"])
            self.engine.set_value(ENGINE_LABELS["webkit"])
            self.quality.set_value(QUALITY_LABELS["smooth"])
            self.browser_theme.set_value(THEME_LABELS["dark"])
            self.url_edit.setText(START_URL)
        self._update_visibility()
        if not editing:
            self._regenerate_if_auto()  # show the random user-agent of the default mode right away
        # Connected only now: filling in the initial values must not regenerate the user-agent.
        self.device_box.currentTextChanged.connect(self._on_device_change)
        self.name_edit.setFocus()

    # --- layout -------------------------------------------------------------
    def _add_field(self, text: str, widget: QWidget, *, row: int, column: int = 0, span: int = 1) -> list[QWidget]:
        """Places a label and a widget on two rows; returns both so they can be hidden."""
        label = make_label(text, "field")
        self._grid.addWidget(label, row, column, 1, span)
        self._grid.addWidget(widget, row + 1, column, 1, span)
        self._grid.setRowMinimumHeight(row + 1, 46)  # room below each field
        return [label, widget]

    # --- widget state -------------------------------------------------------
    def selected_mode(self) -> str:
        return key_for(MODE_LABELS, self.mode.value())

    def selected_engine(self) -> str:
        return "chromium" if self.selected_mode() == "desktop" else key_for(ENGINE_LABELS, self.engine.value())

    def _update_visibility(self) -> None:
        iphone = self.selected_mode() == "iphone"
        for widget in self._iphone_fields:
            widget.setVisible(iphone)
        for widget in self._quality_fields:
            widget.setVisible(iphone and self.selected_engine() == "webkit")
        # Hidden rows must not keep their minimum height.
        for row in (5, 7):
            self._grid.setRowMinimumHeight(row, 46 if iphone else 0)
        if not iphone:
            hint = "Full Chromium browser, without emulation. Random Chrome or Edge for Windows user-agent."
        elif self.selected_engine() == "webkit":
            hint = (
                'Emulates the iPhone screen and touch. "Smooth" renders at your screen\'s resolution '
                '(smooth scrolling); "Sharp" uses the iPhone\'s (x3), which is slower.'
            )
        else:
            hint = "Emulates the iPhone screen and touch with Chromium."
        self.hint.setText(hint)

    def _on_mode_change(self, _value: str) -> None:
        self._update_visibility()
        self._regenerate_if_auto()

    def _on_engine_change(self, _value: str) -> None:
        self._update_visibility()

    def _on_device_change(self, _text: str) -> None:
        self._regenerate_if_auto()

    # --- user-agent ---------------------------------------------------------
    def _set_user_agent(self, text: str) -> None:
        self.ua_edit.setText(text)
        self.ua_edit.setCursorPosition(0)  # show the beginning of the long user-agent
        self._auto_user_agent = text

    def _user_agent_for_selection(self) -> str:
        if self.selected_mode() == "desktop":
            return desktop_user_agent()
        device = self.device_box.currentText()
        return "" if device == RANDOM else (iphone_user_agent(device) or "")

    def _regenerate_if_auto(self) -> None:
        current = self.ua_edit.text().strip()
        if current and current != self._auto_user_agent:
            return  # user-agent typed by hand: leave it alone
        self._set_user_agent(self._user_agent_for_selection())

    def _randomize_user_agent(self) -> None:
        self._show_error("")
        if self.selected_mode() == "iphone":
            device = self.device_box.currentText()
            if device == RANDOM:
                used = {p.device for p in load_profiles().values()}
                self.device_box.blockSignals(True)  # the user-agent is set right below
                self.device_box.setCurrentText(pick_device(used, self.devices))
                self.device_box.blockSignals(False)
            elif device not in IPHONE_MODELS:
                self._show_error(f"{device} is an older model with a fixed user-agent. Pick an iPhone 12 or later.")
                return
        self._set_user_agent(self._user_agent_for_selection())

    # --- save ---------------------------------------------------------------
    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(bool(text))

    def _save(self) -> None:
        device = self.device_box.currentText()
        profile = Profile(
            name=self.name_edit.text().strip(),
            device="" if device == RANDOM else device,
            engine=self.selected_engine(),
            mode=self.selected_mode(),
            quality=key_for(QUALITY_LABELS, self.quality.value()),
            theme=key_for(THEME_LABELS, self.browser_theme.value()),
            locale=self.locale_box.currentText(),
            timezone=self.timezone_box.currentText(),
            start_url=self.url_edit.text(),
            user_agent=self.ua_edit.text(),
        )
        try:
            self.saved = self.controller.save_profile(profile, self.original.name if self.original else None)
        except ProfileError as e:
            self._show_error(str(e))
            return
        self.accept()
