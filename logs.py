"""
Application logging.

Every module logs through a child of the "app" logger (logging.getLogger("app.<module>")).
setup_logging() sends those records to:

- data/logs/app.log: rotating text file that survives crashes.
- PanelHandler: in-memory buffer that feeds the Logs window in real time.
- The terminal, when asked for and when there is one.

It also records any unhandled exception, in the main thread or in any other thread.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
import threading
from collections import deque
from types import TracebackType

from profiles import DATA_DIR

LOGS_DIR = DATA_DIR / "logs"
LOG_FILE = LOGS_DIR / "app.log"
LINE_FORMAT = "%(asctime)s.%(msecs)03d  %(levelname)-8s  %(name)-13s  %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

log = logging.getLogger("app")


class PanelHandler(logging.Handler):
    """Keeps the latest formatted records, plus the ones the interface has not shown yet.

    Each entry is (level, formatted line). Both buffers are bounded, so memory stays
    constant even when nobody reads them (e.g. from the command line).
    """

    def __init__(self, capacity: int = 5000):
        super().__init__(logging.DEBUG)
        self._records: deque[tuple[int, str]] = deque(maxlen=capacity)
        self._pending: deque[tuple[int, str]] = deque(maxlen=capacity)
        self._buffers_lock = threading.Lock()  # records arrive from several threads

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = (record.levelno, self.format(record))
        except Exception:  # standard logging pattern: a bad record must never break the app
            self.handleError(record)
            return
        with self._buffers_lock:
            self._records.append(entry)
            self._pending.append(entry)

    def drain(self) -> list[tuple[int, str]]:
        """New entries since the last call."""
        with self._buffers_lock:
            entries = list(self._pending)
            self._pending.clear()
            return entries

    def records(self, min_level: int = logging.NOTSET) -> list[tuple[int, str]]:
        with self._buffers_lock:
            return [entry for entry in self._records if entry[0] >= min_level]

    def snapshot(self, min_level: int = logging.NOTSET) -> list[tuple[int, str]]:
        """Like records(), but also discards the pending entries, since the caller
        shows everything at once (avoids showing an entry twice)."""
        with self._buffers_lock:
            self._pending.clear()
            return [entry for entry in self._records if entry[0] >= min_level]

    def clear(self) -> None:
        with self._buffers_lock:
            self._records.clear()
            self._pending.clear()


_panel_handler: PanelHandler | None = None


def setup_logging(*, console: bool = False) -> PanelHandler:
    """Configures the "app" logger once and returns the handler of the Logs window.

    console=True also prints INFO and above to the terminal when there is one
    (not with pythonw), e.g. in development mode.
    """
    global _panel_handler
    if _panel_handler is not None:
        return _panel_handler

    formatter = logging.Formatter(LINE_FORMAT, DATE_FORMAT)
    log.setLevel(logging.DEBUG)
    log.propagate = False

    _panel_handler = PanelHandler()
    _panel_handler.setFormatter(formatter)
    log.addHandler(_panel_handler)

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        log.addHandler(file_handler)
    except OSError as e:
        log.warning("The log file could not be opened: %s", e)

    if console and sys.stderr is not None:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        log.addHandler(console_handler)

    _install_exception_hooks()
    log.info("Session started (Python %s, %s)", platform.python_version(), platform.platform())
    return _panel_handler


def _install_exception_hooks() -> None:
    def on_exception(exc_type: type[BaseException], exc: BaseException, traceback: TracebackType | None) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, traceback)
            return
        log.critical("Unhandled exception", exc_info=(exc_type, exc, traceback))

    def on_thread_exception(args: threading.ExceptHookArgs) -> None:
        thread = args.thread.name if args.thread else "unknown"
        log.critical(
            "Unhandled exception in thread %s",
            thread,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),  # type: ignore[arg-type]
        )

    sys.excepthook = on_exception
    threading.excepthook = on_thread_exception
