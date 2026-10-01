"""Asks GitHub for the latest release, and downloads an update, in the background."""

from __future__ import annotations

import contextlib
import json
import logging
import threading
import urllib.request
from typing import cast

from PySide6.QtCore import QObject, Signal, SignalInstance

import updater
from about import APP_NAME, APP_VERSION, LATEST_RELEASE_API, RELEASES_URL

log = logging.getLogger("app.gui")

TIMEOUT_SECONDS = 10


def fetch_latest_release() -> tuple[str, str]:
    """(tag, page url) of the latest release. Raises OSError or ValueError."""
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{APP_VERSION}"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        raw: object = json.loads(response.read().decode("utf-8"))
    data = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    tag, url = data.get("tag_name"), data.get("html_url")
    if not isinstance(tag, str) or not tag:
        raise ValueError("the answer has no tag_name")
    return tag, url if isinstance(url, str) and url.startswith("https://") else RELEASES_URL


class ReleaseChecker(QObject):
    """check() runs in a thread; found or failed arrive on the interface thread."""

    found = Signal(str, str)  # tag, page url
    failed = Signal(str)

    def check(self) -> None:
        threading.Thread(target=self._run, name="release-check", daemon=True).start()

    def _run(self) -> None:
        try:
            tag, url = fetch_latest_release()
        except (OSError, ValueError) as e:  # no connection, GitHub limits, unexpected answer...
            log.info("Could not check the latest release: %s", e)
            self._emit(self.failed, str(e))
            return
        log.info("Latest release on GitHub: %s", tag)
        self._emit(self.found, tag, url)

    @staticmethod
    def _emit(signal: SignalInstance, *args: str) -> None:
        with contextlib.suppress(RuntimeError):  # the app closed while GitHub was answering
            signal.emit(*args)


class UpdateDownloader(QObject):
    """Downloads and checks the new .exe in a thread (see updater.py)."""

    progress = Signal(int, int)  # bytes done, total
    finished = Signal(str)  # path of the checked .exe
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.running = False

    def start(self) -> None:
        if not self.running:
            self.running = True
            threading.Thread(target=self._run, name="update", daemon=True).start()

    def _run(self) -> None:
        try:
            path = updater.download_update(lambda done, total: self.progress.emit(done, total))
        except updater.UpdateError as e:
            log.warning("Could not update: %s", e)
            self.failed.emit(str(e))
        else:
            self.finished.emit(str(path))
        finally:
            self.running = False
