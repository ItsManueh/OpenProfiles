"""
Colors, fonts, icons and the Qt style sheet of the app, for the dark and light themes.

The look follows the default theme of SpotiFLAC (github.com/spotbye/SpotiFLAC, MIT
license): shadcn/ui's "neutral" base with the "yellow" accent and notifications tinted
by type. The fonts are those of shadcn/ui: Geist for the interface and Geist Mono for
numbers and monospaced text.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from profiles import FONTS_DIR
from ui import line_icons

log = logging.getLogger("app.gui")

# shadcn/ui "neutral" + "yellow", converted from OKLCH (the colors of Tailwind v4).
PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "background": "#0a0a0a",
        "surface": "#171717",  # cards, inputs, popovers
        "surface_hover": "#262626",
        "border": "#2e2e2e",  # white at 10 % over a card
        "border_strong": "#3a3a3a",  # inputs: white at 15 %
        "text": "#fafafa",
        "muted": "#a1a1a1",
        "disabled": "#525252",
        "ring": "#737373",
        "accent": "#f0b100",  # yellow: active icons, focus
        "accent_soft": "#432004",
        "accent_text": "#fdc700",
        "primary": "#f0b100",
        "primary_hover": "#fdc700",
        "primary_text": "#733e0a",
        "segment": "#262626",
        "segment_selected": "#3a3a3a",
        "danger": "#ff6467",
        "danger_hover": "#2a1213",
        "danger_fill": "#9d4042",
        "danger_fill_hover": "#b54a4c",
        "opened": "#00c950",
        "opening": "#fe9a00",
        "closed": "#404040",
        "shadow": "#000000",
        # Notifications: background, border, title, icon and detail of each tone.
        "toast_success": "#032e15,#016630,#dcfce7,#05df72,#16a34a",
        "toast_error": "#460809,#9f0712,#ffe2e2,#ff6467,#dc2626",
        "toast_warning": "#432004,#894b00,#fef9c2,#fdc700,#ca8a04",
        "toast_info": "#162456,#193cb8,#dbeafe,#51a2ff,#2563eb",
        "toast_neutral": "#171717,#2e2e2e,#fafafa,#a1a1a1,#a1a1a1",
    },
    "light": {
        "background": "#ffffff",
        "surface": "#ffffff",
        "surface_hover": "#f5f5f5",
        "border": "#e5e5e5",
        "border_strong": "#e5e5e5",
        "text": "#0a0a0a",
        "muted": "#737373",
        "disabled": "#a3a3a3",
        "ring": "#a1a1a1",
        "accent": "#d08700",
        "accent_soft": "#fef9c2",
        "accent_text": "#733e0a",
        "primary": "#fdc700",
        "primary_hover": "#f0b100",
        "primary_text": "#733e0a",
        "segment": "#f5f5f5",
        "segment_selected": "#ffffff",
        "danger": "#e7000b",
        "danger_hover": "#fef2f2",
        "danger_fill": "#e7000b",
        "danger_fill_hover": "#c10007",
        "opened": "#00a63e",
        "opening": "#e17100",
        "closed": "#d4d4d4",
        "shadow": "#000000",
        "toast_success": "#f0fdf4,#b9f8cf,#0d542b,#00a63e,#16a34a",
        "toast_error": "#fef2f2,#ffc9c9,#82181a,#e7000b,#dc2626",
        "toast_warning": "#fefce8,#fff085,#733e0a,#d08700,#ca8a04",
        "toast_info": "#eff6ff,#bedbff,#1c398e,#155dfc,#2563eb",
        "toast_neutral": "#ffffff,#e5e5e5,#0a0a0a,#737373,#737373",
    },
}
TONES = ("success", "error", "warning", "info", "neutral")
SHADOW_ALPHA = {"dark": 0.55, "light": 0.10}  # notification shadow ("shadow-lg")

# Log colors that read well on both themes (the text of the logs keeps them when the theme changes).
LOG_COLORS = {"debug": "#8b8b94", "warning": "#d97706", "error": "#ef4444"}
# Labels a profile can have (Tailwind's 500 shades, readable on both themes).
LABEL_COLORS = {
    "red": "#ef4444",
    "orange": "#f97316",
    "yellow": "#eab308",
    "green": "#22c55e",
    "blue": "#3b82f6",
    "purple": "#a855f7",
    "pink": "#ec4899",
}
LOG_TONE_COLORS = {
    "success": "#16a34a",
    "info": "#2563eb",
    "warning": "#ca8a04",
    "error": "#dc2626",
    "neutral": "#8b8b94",
}

# Glyphs of the Windows icon font (Segoe Fluent Icons, or Segoe MDL2 Assets on
# Windows 10) for the window buttons; every other icon is a line icon (line_icons.py).
ICONS = {
    "minimize": "\ue921",
    "maximize": "\ue922",
    "restore": "\ue923",
    "close": "\ue8bb",
}

# Icon and tone of each kind of event, shared by the notifications and the logs window.
EVENTS: dict[str, tuple[str, str]] = {
    "created": ("circle-check", "success"),
    "edited": ("circle-check", "success"),
    "deleted": ("trash", "neutral"),
    "opened": ("circle-play", "info"),
    "closed": ("power", "neutral"),
    "downloaded": ("download", "success"),
    "file": ("download", "success"),
    "folder": ("folder", "neutral"),
    "copied": ("copy", "neutral"),
    "saved": ("save", "success"),
    "cleared": ("eraser", "neutral"),
    "info": ("info", "info"),
    "warning": ("triangle-alert", "warning"),
    "error": ("circle-alert", "error"),
    "critical": ("circle-alert", "error"),
}

_mode = "dark"


@dataclass
class FontFamilies:
    """Font families in use; load_fonts() switches to the bundled Geist fonts."""

    text: str = "Segoe UI"
    mono: str = "Consolas"
    icons: str = "Segoe MDL2 Assets"


FONTS = FontFamilies()


def mode() -> str:
    return _mode


def color(name: str) -> QColor:
    """Color of the current theme, for widgets that paint themselves."""
    return QColor(PALETTES[_mode][name])


def toast_colors(tone: str, theme: str | None = None) -> list[str]:
    """[background, border, title, icon, detail] of a notification tone."""
    return PALETTES[theme or _mode][f"toast_{tone}"].split(",")


def icon(name: str) -> str:
    return ICONS[name]


# ----------------------------------------------------------------------------
# Fonts: Geist (interface) and Geist Mono (numbers and monospaced text), the
# fonts of shadcn/ui, bundled with the app (SIL OFL license) so they look the
# same on every computer. The window buttons use the Windows icon font.
# ----------------------------------------------------------------------------

BUNDLED_FONTS = {"Geist.ttf": "Geist", "GeistMono.ttf": "Geist Mono"}


def load_fonts() -> None:
    for file, family in BUNDLED_FONTS.items():
        if QFontDatabase.addApplicationFont(str(FONTS_DIR / file)) < 0:
            log.warning("Could not load the font %s", family)
    families = set(QFontDatabase.families())
    if "Geist" in families:
        FONTS.text = "Geist"
    if "Geist Mono" in families:
        FONTS.mono = "Geist Mono"
    elif "Cascadia Mono" in families:
        FONTS.mono = "Cascadia Mono"
    FONTS.icons = "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else "Segoe MDL2 Assets"
    log.debug("Fonts: %s, %s, icons %s", FONTS.text, FONTS.mono, FONTS.icons)


def mono_font(size: int = 12) -> QFont:
    font = QFont(FONTS.mono)
    font.setPixelSize(size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def icon_font(size: int) -> QFont:
    font = QFont(FONTS.icons)
    font.setPixelSize(size)
    return font


# ----------------------------------------------------------------------------
# Style sheet
# ----------------------------------------------------------------------------

CHEVRON_VERSION = 2  # part of the file name: a new drawing never reuses an old file


def _chevron_file(color_name: str) -> str:
    """PNG of the "chevron down" icon in that color, for the arrow of the drop-down lists
    (style sheets only take images from files). Cached in the temporary folder."""
    folder = Path(tempfile.gettempdir()) / "OpenProfiles"
    path = folder / f"chevron-v{CHEVRON_VERSION}-{color_name.lstrip('#')}.png"
    if not path.exists():
        pixmap = line_icons.pixmap("chevron-down", QColor(color_name), 16)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            pixmap.save(str(path), "PNG")
        except OSError as e:
            log.debug("Could not save the drop-down arrow: %s", e)  # Qt then shows no arrow
    return path.as_posix()


def _toast_rules(theme: str) -> str:
    rules: list[str] = []
    for tone in TONES:
        background, border, title, _icon, detail = toast_colors(tone, theme)
        rules.append(
            f'QFrame#toast[tone="{tone}"] {{ background: {background}; border: 1px solid {border}; }}\n'
            f'QFrame#toast[tone="{tone}"] QLabel#toastTitle {{ color: {title}; }}\n'
            f'QFrame#toast[tone="{tone}"] QLabel#toastDetail {{ color: {detail}; }}\n'
            f'QFrame#toast[tone="{tone}"] QPushButton#toastAction {{ color: {title}; border: 1px solid {border}; }}\n'
            f'QFrame#toast[tone="{tone}"] QPushButton#toastAction:hover {{ background: {border}; }}'
        )
    return "\n".join(rules)


def stylesheet(theme: str) -> str:
    c = PALETTES[theme]
    return f"""
    QWidget {{
        color: {c["text"]};
        font-family: "{FONTS.text}";
        font-size: 14px;
    }}
    QMainWindow, QDialog, QWidget#LogWindow, QWidget#central {{ background: {c["background"]}; }}
    QToolTip {{
        background: {c["surface"]}; color: {c["text"]}; border: 1px solid {c["border"]}; padding: 5px 8px;
    }}
    QMenu {{ background: {c["surface"]}; color: {c["text"]}; border: 1px solid {c["border"]}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {c["surface_hover"]}; }}
    QMenu::separator {{ height: 1px; background: {c["border"]}; margin: 4px 6px; }}

    QLabel#title {{ font-size: 28px; font-weight: 700; }}
    QLabel#dialogTitle, QLabel#pageTitle {{ font-size: 22px; font-weight: 700; }}
    QLabel#subtitle {{ color: {c["muted"]}; font-size: 14px; }}
    QLabel#emptyTitle {{ font-size: 17px; font-weight: 600; }}
    QLabel#profileName {{ font-size: 15px; font-weight: 600; }}
    QLabel#stateLabel {{ color: {c["muted"]}; font-size: 12px; font-weight: 500; }}
    QLabel#stateLabel[state="opened"] {{ color: {c["opened"]}; }}
    QLabel#stateLabel[state="opening"], QLabel#stateLabel[state="closing"] {{ color: {c["opening"]}; }}
    QLabel[role="muted"] {{ color: {c["muted"]}; }}
    QLabel[role="small"] {{ color: {c["muted"]}; font-size: 13px; }}
    QLabel[role="field"] {{ font-size: 13px; font-weight: 500; }}
    QLabel[role="error"] {{ color: {c["danger"]}; font-size: 13px; }}

    QFrame#card {{ background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 12px; }}
    QFrame#card:hover {{ border-color: {c["border_strong"]}; }}
    QFrame#card[dragging="true"] {{ border-color: {c["accent"]}; background: {c["surface_hover"]}; }}
    QFrame#dropSlot {{ border: 1px dashed {c["border_strong"]}; border-radius: 12px; background: transparent; }}
    QFrame#card QLabel {{ background: transparent; }}

    QFrame#toast {{ border-radius: 10px; }}
    QFrame#toast QLabel, QFrame#toast QWidget {{ background: transparent; }}
    QLabel#toastTitle {{ font-family: "{FONTS.mono}"; font-size: 13px; font-weight: 500; }}
    QLabel#toastDetail {{ font-family: "{FONTS.mono}"; font-size: 12px; }}
    QPushButton#toastAction {{
        min-height: 28px; max-height: 28px; padding: 0 8px; border-radius: 6px; background: transparent;
        font-family: "{FONTS.mono}"; font-size: 12px; font-weight: 600;
    }}
    {_toast_rules(theme)}

    QPushButton {{
        border-radius: 8px; padding: 0 16px; min-height: 36px; font-size: 14px; font-weight: 500;
        background: transparent; border: none; color: {c["text"]};
    }}
    QPushButton[size="small"] {{ min-height: 32px; padding: 0 12px; font-size: 13px; }}
    QPushButton:disabled {{ color: {c["disabled"]}; }}
    QPushButton[variant="primary"] {{ background: {c["primary"]}; color: {c["primary_text"]}; }}
    QPushButton[variant="primary"]:hover {{ background: {c["primary_hover"]}; }}
    QPushButton[variant="primary"]:disabled {{ background: {c["segment"]}; color: {c["disabled"]}; }}
    QPushButton[variant="secondary"] {{ background: {c["surface"]}; border: 1px solid {c["border_strong"]}; }}
    QPushButton[variant="secondary"]:hover {{ background: {c["surface_hover"]}; }}
    QPushButton[variant="secondary"]:disabled {{ background: transparent; border-color: {c["border"]}; }}
    QPushButton[variant="ghost"], QPushButton[variant="ghost_danger"] {{ color: {c["muted"]}; }}
    QPushButton[variant="ghost"]:hover {{ background: {c["surface_hover"]}; color: {c["text"]}; }}
    QPushButton[variant="ghost_danger"]:hover {{ background: {c["danger_hover"]}; color: {c["danger"]}; }}
    QPushButton[variant="ghost"]:disabled, QPushButton[variant="ghost_danger"]:disabled {{ color: {c["disabled"]}; }}
    QPushButton[variant="danger"] {{ background: {c["danger_fill"]}; color: #ffffff; }}
    QPushButton[variant="danger"]:hover {{ background: {c["danger_fill_hover"]}; }}

    QLineEdit, QComboBox {{
        background: {c["surface"]}; border: 1px solid {c["border_strong"]}; border-radius: 8px;
        padding: 0 10px; min-height: 36px; selection-background-color: {c["accent_soft"]};
        selection-color: {c["text"]};
    }}
    QLineEdit:hover, QComboBox:hover {{ border-color: {c["ring"]}; }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {c["accent"]}; }}
    QComboBox::drop-down {{ border: none; width: 30px; }}
    QComboBox::down-arrow {{ image: url("{_chevron_file(c["muted"])}"); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{
        background: {c["surface"]}; border: 1px solid {c["border"]}; outline: 0; padding: 4px;
        selection-background-color: {c["surface_hover"]}; selection-color: {c["text"]};
    }}

    QScrollArea, QWidget#profileList, QWidget#page {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {c["border_strong"]}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
    QScrollBar::handle:hover {{ background: {c["ring"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QLineEdit#logSearch {{ min-height: 32px; font-size: 13px; }}
    QPlainTextEdit#logView {{
        background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 10px; padding: 6px;
        font-family: "{FONTS.mono}"; font-size: 12px; selection-background-color: {c["segment_selected"]};
    }}

    QPushButton#releaseBadge {{
        background: {c["accent_soft"]}; color: {c["accent_text"]}; border-radius: 11px;
        min-height: 22px; max-height: 22px; padding: 0 9px; font-family: "{FONTS.mono}"; font-size: 12px;
        font-weight: 600;
    }}
    QPushButton#releaseBadge:hover, QPushButton#releaseBadge[update="true"] {{
        background: {c["primary"]}; color: {c["primary_text"]};
    }}

    QLabel#sectionTitle {{ color: {c["muted"]}; font-size: 12px; font-weight: 600; letter-spacing: 0.6px; }}
    QLabel#tabTitle {{ font-size: 18px; font-weight: 600; }}
    QLabel#windowTitle {{ font-size: 13px; font-weight: 600; color: {c["muted"]}; }}
    QFrame#settingsGroup {{ background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 12px; }}
    QFrame#settingsGroup QLabel {{ background: transparent; }}
    QFrame#settingsDivider {{ background: {c["border"]}; border: none; min-height: 1px; max-height: 1px; }}
    QLabel#settingName {{ font-size: 14px; font-weight: 600; }}
    """


# ----------------------------------------------------------------------------
# Appearance modes: auto (follows Windows), light or dark
# ----------------------------------------------------------------------------

_setting = "auto"  # what the user picked; _mode is the theme actually in use
_listeners: list[Callable[[str], None]] = []
_watching_system = False
_styled = False  # whether a style sheet was applied yet


def setting() -> str:
    return _setting


def on_theme_changed(listener: Callable[[str], None]) -> None:
    """Calls listener(theme) every time the theme in use changes."""
    _listeners.append(listener)


def _system_theme(app: QApplication) -> str:
    return "light" if app.styleHints().colorScheme() == Qt.ColorScheme.Light else "dark"


def apply_mode(mode: str) -> None:
    """Applies "auto", "light" or "dark" to the whole app, dialog title bars included."""
    global _setting, _watching_system
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    _setting = mode
    hints = app.styleHints()
    if mode == "auto":
        hints.setColorScheme(Qt.ColorScheme.Unknown)  # back to the Windows setting
        theme = _system_theme(app)
    else:
        hints.setColorScheme(Qt.ColorScheme.Dark if mode == "dark" else Qt.ColorScheme.Light)
        theme = mode
    if not _watching_system:
        hints.colorSchemeChanged.connect(_on_system_scheme_changed)
        _watching_system = True
    # Going back to "auto" may have applied the Windows theme already (colorSchemeChanged).
    if theme != _mode or not _styled:
        _apply(app, theme)


def _on_system_scheme_changed(_scheme: Qt.ColorScheme) -> None:
    app = QApplication.instance()
    if _setting == "auto" and isinstance(app, QApplication) and _system_theme(app) != _mode:
        _apply(app, _system_theme(app))


def _apply(app: QApplication, theme: str) -> None:
    global _mode, _styled
    _mode = theme
    _styled = True
    app.setStyleSheet(stylesheet(theme))
    for widget in app.allWidgets():
        widget.update()  # widgets that paint themselves read the new colors
    log.debug("Theme in use: %s (mode %s)", theme, _setting)
    for listener in _listeners:
        listener(theme)
