"""Colors, fonts and the Qt style sheet of the app, for the dark and light themes."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from profiles import FONTS_DIR

log = logging.getLogger("app.gui")

PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "background": "#0e0e10",
        "surface": "#18181b",
        "surface_hover": "#232327",
        "border": "#2e2e33",
        "text": "#fafafa",
        "muted": "#c4c4cc",
        "disabled": "#5c5c66",
        "primary": "#fafafa",
        "primary_hover": "#d4d4d8",
        "primary_text": "#18181b",
        "segment": "#27272a",
        "segment_selected": "#46464f",
        "danger": "#f87171",
        "danger_hover": "#2a1515",
        "danger_fill": "#dc2626",
        "danger_fill_hover": "#ef4444",
        "opened": "#22c55e",
        "opening": "#f59e0b",
        "closed": "#3f3f46",
        "switch_knob": "#18181b",
        "switch_knob_off": "#fafafa",
    },
    "light": {
        "background": "#f5f5f7",
        "surface": "#ffffff",
        "surface_hover": "#f4f4f5",
        "border": "#e4e4e7",
        "text": "#18181b",
        "muted": "#52525b",
        "disabled": "#a1a1aa",
        "primary": "#18181b",
        "primary_hover": "#3f3f46",
        "primary_text": "#ffffff",
        "segment": "#e4e4e7",
        "segment_selected": "#ffffff",
        "danger": "#dc2626",
        "danger_hover": "#fee2e2",
        "danger_fill": "#dc2626",
        "danger_fill_hover": "#b91c1c",
        "opened": "#16a34a",
        "opening": "#d97706",
        "closed": "#d4d4d8",
        "switch_knob": "#ffffff",
        "switch_knob_off": "#ffffff",
    },
}
# Log colors that read well on both themes.
LOG_COLORS = {"debug": "#8b8b94", "warning": "#d97706", "error": "#ef4444"}

_mode = "dark"


@dataclass
class FontFamilies:
    """Font families in use; load_fonts() switches them to Inter when it is available."""

    text: str = "Segoe UI"
    title: str = "Segoe UI"
    mono: str = "Consolas"


FONTS = FontFamilies()


def mode() -> str:
    return _mode


def color(name: str) -> QColor:
    """Color of the current theme, for widgets that paint themselves."""
    return QColor(PALETTES[_mode][name])


# ----------------------------------------------------------------------------
# Fonts: Inter, the closest free alternative to SF Pro (the iPhone font, whose
# license forbids using it outside Apple devices). Loaded for this app only,
# without installing it on Windows. Segoe UI is the fallback.
# ----------------------------------------------------------------------------


def load_fonts() -> None:
    for path in sorted(FONTS_DIR.glob("*.ttf")):
        if QFontDatabase.addApplicationFont(str(path)) < 0:
            log.warning("Could not load font %s", path.name)
    families = set(QFontDatabase.families())
    if "Inter" in families:
        FONTS.text = "Inter"
        FONTS.title = "Inter Display" if "Inter Display" in families else "Inter"
    if "Cascadia Mono" in families:
        FONTS.mono = "Cascadia Mono"
    log.debug("Fonts: %s / %s / %s", FONTS.text, FONTS.title, FONTS.mono)


def mono_font(size: int = 12) -> QFont:
    font = QFont(FONTS.mono)
    font.setPixelSize(size)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


# ----------------------------------------------------------------------------
# Style sheet
# ----------------------------------------------------------------------------


def stylesheet(theme: str) -> str:
    c = PALETTES[theme]
    return f"""
    QWidget {{
        color: {c["text"]};
        font-family: "{FONTS.text}";
        font-size: 14px;
    }}
    QMainWindow, QDialog, QWidget#LogWindow, QWidget#central {{ background: {c["background"]}; }}
    QToolTip {{ background: {c["surface"]}; color: {c["text"]}; border: 1px solid {c["border"]}; padding: 4px; }}

    QLabel#title {{ font-family: "{FONTS.title}"; font-size: 30px; font-weight: 700; }}
    QLabel#dialogTitle {{ font-family: "{FONTS.title}"; font-size: 22px; font-weight: 700; }}
    QLabel#subtitle {{ color: {c["muted"]}; font-size: 15px; }}
    QLabel#emptyTitle {{ font-size: 18px; font-weight: 600; }}
    QLabel#profileName {{ font-size: 16px; font-weight: 600; }}
    QLabel[role="muted"] {{ color: {c["muted"]}; }}
    QLabel[role="small"] {{ color: {c["muted"]}; font-size: 13px; }}
    QLabel[role="field"] {{ font-size: 13px; font-weight: 600; }}
    QLabel[role="error"] {{ color: {c["danger"]}; font-size: 13px; }}
    QLabel[role="status-error"] {{ color: {c["danger"]}; font-size: 13px; }}

    QFrame#card {{ background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 12px; }}
    QFrame#card QLabel {{ background: transparent; }}

    QPushButton {{
        border-radius: 8px; padding: 0 16px; min-height: 38px; font-size: 14px;
        background: transparent; border: none; color: {c["text"]};
    }}
    QPushButton[size="small"] {{ min-height: 30px; padding: 0 12px; }}
    QPushButton:disabled {{ color: {c["disabled"]}; }}
    QPushButton[variant="primary"] {{ background: {c["primary"]}; color: {c["primary_text"]}; font-weight: 600; }}
    QPushButton[variant="primary"]:hover {{ background: {c["primary_hover"]}; }}
    QPushButton[variant="primary"]:disabled {{ color: {c["disabled"]}; }}
    QPushButton[variant="secondary"] {{ border: 1px solid {c["border"]}; }}
    QPushButton[variant="secondary"]:hover {{ background: {c["surface_hover"]}; }}
    QPushButton[variant="ghost"], QPushButton[variant="ghost_danger"] {{ color: {c["muted"]}; }}
    QPushButton[variant="ghost"]:hover {{ background: {c["surface_hover"]}; }}
    QPushButton[variant="ghost_danger"]:hover {{ background: {c["danger_hover"]}; }}
    QPushButton[variant="ghost"]:disabled, QPushButton[variant="ghost_danger"]:disabled {{ color: {c["disabled"]}; }}
    QPushButton[variant="danger"] {{ background: {c["danger_fill"]}; color: #ffffff; font-weight: 600; }}
    QPushButton[variant="danger"]:hover {{ background: {c["danger_fill_hover"]}; }}
    QPushButton[alert="true"] {{ color: {c["danger"]}; }}

    QFrame#segmented {{ background: {c["segment"]}; border-radius: 10px; }}
    QPushButton#segment {{ min-height: 32px; border-radius: 8px; padding: 0 14px; }}
    QFrame#segmented[size="small"] QPushButton#segment {{ min-height: 24px; padding: 0 10px; }}
    QPushButton#segment:checked {{ background: {c["segment_selected"]}; }}

    QLineEdit, QComboBox {{
        background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 8px;
        padding: 0 10px; min-height: 36px; selection-background-color: {c["segment_selected"]};
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {c["muted"]}; }}
    QComboBox QAbstractItemView {{
        background: {c["surface"]}; border: 1px solid {c["border"]}; outline: 0; padding: 4px;
        selection-background-color: {c["surface_hover"]}; selection-color: {c["text"]};
    }}

    QScrollArea, QWidget#profileList {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {c["border"]}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
    QScrollBar::handle:hover {{ background: {c["muted"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QPlainTextEdit#logView {{
        background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 10px; padding: 6px;
        font-family: "{FONTS.mono}"; font-size: 12px; selection-background-color: {c["segment_selected"]};
    }}
    """


def apply_theme(theme: str) -> None:
    """Applies the theme to the whole app, including the Windows title bars."""
    global _mode
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    _mode = theme
    app.setStyleSheet(stylesheet(theme))
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark if theme == "dark" else Qt.ColorScheme.Light)
    for widget in app.allWidgets():
        widget.update()  # widgets that paint themselves read the new colors
