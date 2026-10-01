"""Builds the Qt application and starts everything."""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys
from types import TracebackType

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

import logs
from about import APP_NAME, APP_VERSION
from profiles import DATA_DIR, ICON_FILE, migrate_legacy_data
from settings import load_settings
from ui import theme
from ui.controller import AppController
from ui.main_window import MainWindow
from ui.single_instance import SingleInstance

log = logging.getLogger("app.gui")
APP_ID = APP_NAME


def create_app(argv: list[str] | None = None) -> tuple[QApplication, MainWindow]:
    """Creates the QApplication and the main window, and starts the browser engine."""
    migration = migrate_legacy_data()  # before logging, which already writes into the data folder
    log_handler = logs.setup_logging(console=True)  # the terminal only exists without pythonw
    if migration is not None:
        log.log(*migration)
    log.info("Starting %s %s (data folder: %s)", APP_NAME, APP_VERSION, DATA_DIR)
    if sys.platform == "win32":
        # Own taskbar identity, so Windows shows the app icon instead of Python's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)

    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication(argv if argv is not None else sys.argv)
    instance = SingleInstance(DATA_DIR, app)
    if instance.notify_running_instance():
        log.info("The app is already running; brought its window to the front")
        raise SystemExit(0)
    instance.listen()
    app.setApplicationName(APP_ID)
    app.setStyle("Fusion")  # same look on every Windows version; the style sheet does the rest
    if ICON_FILE.exists():
        app.setWindowIcon(QIcon(str(ICON_FILE)))
    theme.load_fonts()
    settings = load_settings()
    theme.apply_mode(settings.mode)

    controller = AppController()
    window = MainWindow(controller, log_handler, settings)
    instance.activation_requested.connect(window.bring_to_front)
    _report_unhandled_errors(window)
    window.show()
    window.bring_to_front()  # in front of the window that was active while the app started
    controller.start()
    return app, window


def _report_unhandled_errors(window: MainWindow) -> None:
    """Errors raised inside Qt slots reach sys.excepthook (which logs them); also show them."""
    previous = sys.excepthook

    def hook(exc_type: type[BaseException], exc: BaseException, traceback: TracebackType | None) -> None:
        previous(exc_type, exc, traceback)
        if not issubclass(exc_type, KeyboardInterrupt):
            # While the app closes the window may already be gone; the error is logged anyway.
            with contextlib.suppress(RuntimeError):
                window.show_message(f"Unexpected error: {exc}", error=True)

    sys.excepthook = hook


def main() -> int:
    app, _window = create_app()
    return app.exec()
