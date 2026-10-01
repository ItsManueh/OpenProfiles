"""
What the browsers forget when they close, kept for the next time:

- Session cookies: cookies without an expiry date, which many sites use to keep you
  logged in, are deleted by a browser when it closes. They are saved while the
  profile is open and put back when it opens again.
- The open tabs, to reopen them where you left off.
- Downloads: Playwright deletes them when the window closes, so every download is
  copied to the Downloads folder.

Cookies and tabs are stored encrypted (see secure_store.py) in the profile folder.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

if TYPE_CHECKING:
    from playwright._impl._api_structures import SetCookieParam

import secure_store

log = logging.getLogger("app.launcher")

SESSION_FILE = "openprofiles-session.bin"
SAVE_EVERY_SECONDS = 30  # also saved after every page load and when the app closes the profile
SAVE_AFTER_LOAD_SECONDS = 2  # a page sets its cookies right after loading
TAB_CLOSE_GRACE_SECONDS = 1.5  # closing the window closes the tabs one by one: they are all kept
MAX_TABS = 20


@dataclass
class SavedSession:
    cookies: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    tabs: list[str] = field(default_factory=list[str])


def safe_url(url: str) -> str:
    """The address without its query and fragment, which can hold tokens: for the logs."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.scheme in ("http", "https") else url[:60]


def load_session(folder: Path) -> SavedSession:
    """What was saved last time; an unreadable file (another Windows user, damaged) is ignored."""
    try:
        data = secure_store.read(folder / SESSION_FILE)
        if data is None:
            return SavedSession()
        raw: object = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as e:
        log.warning("Could not read the saved session in %s: %s", folder.name, e)
        return SavedSession()
    values = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    cookies = cast("list[object]", values.get("cookies")) if isinstance(values.get("cookies"), list) else []
    tabs = cast("list[object]", values.get("tabs")) if isinstance(values.get("tabs"), list) else []
    return SavedSession(
        [cast("dict[str, Any]", cookie) for cookie in cookies if isinstance(cookie, dict)],
        [tab for tab in tabs if isinstance(tab, str)],
    )


class SessionKeeper:
    """Follows a profile's tabs and session cookies and saves them, encrypted."""

    def __init__(self, context: BrowserContext, folder: Path, name: str):
        self.context = context
        self.path = folder / SESSION_FILE
        self.name = name
        self.tabs: dict[Page, str] = {}  # in the order they were opened
        self.cookies: list[dict[str, Any]] = []  # last session cookies read
        self.closed = False
        self._save_soon: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._periodic = self._start(self._save_periodically())

    async def restore(self, saved: SavedSession) -> None:
        """Puts the saved session cookies back (before the first page loads)."""
        self.cookies = saved.cookies
        if not saved.cookies:
            return
        try:
            await self.context.add_cookies([cast("SetCookieParam", cookie) for cookie in saved.cookies])
            log.debug("'%s': restored %d session cookie(s)", self.name, len(saved.cookies))
        except PlaywrightError as e:
            log.warning("'%s': could not restore the session cookies: %s", self.name, e)

    # --- tabs ---------------------------------------------------------------
    def track(self, page: Page) -> None:
        self.tabs[page] = page.url

        def on_navigated(frame: object) -> None:
            if frame is page.main_frame:
                self.tabs[page] = page.url

        def on_load(_page: Page) -> None:
            self.schedule_save(SAVE_AFTER_LOAD_SECONDS)

        def on_close(_page: Page) -> None:
            # Closing the window closes every tab: wait a moment before forgetting one.
            loop = asyncio.get_running_loop()
            loop.call_later(TAB_CLOSE_GRACE_SECONDS, self._forget_tab, page)

        page.on("framenavigated", on_navigated)
        page.on("load", on_load)
        page.on("close", on_close)

    def _forget_tab(self, page: Page) -> None:
        if not self.closed and page in self.tabs:
            del self.tabs[page]
            self.schedule_save(0)

    def open_tabs(self) -> list[str]:
        # The live address of each open tab (a pop-up may navigate before it is followed),
        # and the last one seen of the tabs closed during the grace period.
        urls = [url if page.is_closed() else page.url for page, url in self.tabs.items()]
        return [url for url in urls if url.startswith(("http://", "https://"))][:MAX_TABS]

    # --- saving -------------------------------------------------------------
    def schedule_save(self, delay: float) -> None:
        if self.closed:
            return
        if self._save_soon is not None:
            self._save_soon.cancel()
        loop = asyncio.get_running_loop()
        self._save_soon = loop.call_later(delay, lambda: self._start(self.save()))

    def _start(self, coroutine: Any) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _save_periodically(self) -> None:
        while not self.closed:
            await asyncio.sleep(SAVE_EVERY_SECONDS)
            await self.save()

    async def save(self) -> None:
        """Reads the session cookies (if the browser is still open) and writes everything."""
        if not self.closed:
            try:
                cookies = await self.context.cookies()
                self.cookies = [dict(c) for c in cookies if c.get("expires", -1) == -1]
            except PlaywrightError:
                pass  # the window is closing: the last cookies read are kept
        self._write()

    def _write(self) -> None:
        content = json.dumps({"version": 1, "cookies": self.cookies, "tabs": self.open_tabs()})
        try:
            secure_store.write(self.path, content.encode("utf-8"))
        except OSError as e:
            log.warning("'%s': could not save the session: %s", self.name, e)

    async def close(self) -> None:
        """Last save before the profile closes (or right after, keeping the last cookies read)."""
        if self.closed:
            return
        await self.save()
        self.closed = True
        if self._save_soon is not None:
            self._save_soon.cancel()
        self._periodic.cancel()
        self._write()  # the tabs closed during the grace period are kept


# ----------------------------------------------------------------------------
# Downloads
# ----------------------------------------------------------------------------


def downloads_folder() -> Path:
    """The user's Downloads folder (wherever Windows has it)."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        folder_id = uuid.UUID("{374DE290-123F-4565-9164-39C4925E467B}")  # FOLDERID_Downloads
        guid = (ctypes.c_byte * 16).from_buffer_copy(folder_id.bytes_le)
        path = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.c_void_p]
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path)) == 0 and path.value:
            result = Path(path.value)
            ctypes.windll.ole32.CoTaskMemFree(path)
            return result
    return Path.home() / "Downloads"


INVALID_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def unique_path(folder: Path, file_name: str) -> Path:
    """A free path for the file in the folder: "photo.jpg", then "photo (1).jpg"..."""
    clean = INVALID_FILE_CHARS.sub("_", file_name).strip(" .") or "download"
    path = folder / clean
    stem, suffix = path.stem, path.suffix
    number = 1
    while path.exists():
        path = folder / f"{stem} ({number}){suffix}"
        number += 1
    return path


# ----------------------------------------------------------------------------
# Chromium: no "did not shut down correctly" bar
# ----------------------------------------------------------------------------


def mark_clean_exit(folder: Path) -> None:
    """If the app was closed abruptly, Chromium would offer to restore the pages with a bar
    at the top; the profile is marked as closed normally instead (the app restores its tabs)."""
    preferences = folder / "Default" / "Preferences"
    try:
        raw: object = json.loads(preferences.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # not created yet, or not readable: Chromium handles it
    if not isinstance(raw, dict):
        return
    data = cast("dict[str, Any]", raw)
    profile = data.get("profile")
    if not isinstance(profile, dict):
        return
    settings = cast("dict[str, Any]", profile)
    if settings.get("exit_type") == "Normal" and settings.get("exited_cleanly") is True:
        return
    settings["exit_type"] = "Normal"
    settings["exited_cleanly"] = True
    temp = preferences.with_suffix(".openprofiles.tmp")
    with contextlib.suppress(OSError):
        temp.write_text(json.dumps(data), encoding="utf-8")
        temp.replace(preferences)
