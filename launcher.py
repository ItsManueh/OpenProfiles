"""
Opening profiles with Playwright: browser installation, launch options for each
mode, file chooser, the dialogs and downloads of the pages, and the lifecycle of
each window. Several profiles can be open at the same time.

The anti-detection measures are in stealth.py and what is kept between sessions
(session cookies, tabs, downloads) in browser_session.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import playwright
from playwright.async_api import (
    BrowserContext,
    Dialog,
    Download,
    FileChooser,
    Frame,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

import browser_session
import stealth
from browser_session import SessionKeeper, safe_url
from profiles import BROWSERS_DIR, Devices, Profile, describe

if TYPE_CHECKING:
    import tkinter as tk

log = logging.getLogger("app.launcher")

# notify(profile_name, state, message) with state "opened", "warning", "error", "saved"
# (a download was saved; the message is the file name) or "closed".
Notify = Callable[[str, str, str], None]
# choose_files(multiple, accept) -> paths picked by the user.
ChooseFiles = Callable[[bool, str], Awaitable[list[str]]]
# ask_dialog(kind, message, default_text, site) -> (accepted, text). kind: alert, confirm, prompt
# or beforeunload ("Leave site?"); site is the address of the page that asks.
AskDialog = Callable[[str, str, str, str], Awaitable[tuple[bool, str]]]
# Largest size of the desktop-mode window (it never covers the whole screen).
DESKTOP_WINDOW_MAX = (1440, 900)


# ----------------------------------------------------------------------------
# Browsers, devices and screen
# ----------------------------------------------------------------------------


REQUIRED_BROWSERS = ("webkit", "chromium")


@lru_cache(maxsize=1)
def required_browser_folders() -> list[str]:
    """Folders of the browser builds this version of Playwright needs, e.g. "chromium-1243".
    Empty if Playwright's list cannot be read."""
    try:
        path = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
        browsers = json.loads(path.read_text(encoding="utf-8"))["browsers"]
        revisions = {b["name"]: b["revision"] for b in browsers if b["name"] in REQUIRED_BROWSERS}
        return [f"{name}-{revisions[name]}" for name in REQUIRED_BROWSERS]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def browsers_installed() -> bool:
    """Whether WebKit and Chromium are fully downloaded. Playwright writes INSTALLATION_COMPLETE
    at the end, so a download cut halfway (the app closed, the connection dropped) does not count,
    and neither do the builds of an older Playwright."""
    folders = required_browser_folders()
    if not folders:  # unknown revisions: any complete build will do
        return all(
            any((folder / "INSTALLATION_COMPLETE").exists() for folder in BROWSERS_DIR.glob(f"{name}-*"))
            for name in REQUIRED_BROWSERS
        )
    return all((BROWSERS_DIR / folder / "INSTALLATION_COMPLETE").exists() for folder in folders)


def driver_command() -> list[str]:
    """Node and CLI script of the driver bundled with Playwright (also inside the .exe)."""
    driver = Path(playwright.__file__).parent / "driver"
    node = driver / ("node.exe" if sys.platform == "win32" else "node")
    return [str(node), str(driver / "package" / "cli.js")]


class InstallCancelledError(RuntimeError):
    """The download of the browsers was stopped with cancel_install()."""


_installer: subprocess.Popen[str] | None = None
_installer_cancelled = False
_installer_lock = threading.Lock()
# Lines of Playwright's installer: "Downloading Chrome for Testing 140.0.7339.16 (playwright chromium
# v1187) from ...", "Downloading FFmpeg (playwright ffmpeg v1011) from ..." and, every 10 % of each
# download, "|■■■■■■■■     |  10% of 147.3 MiB".
_DOWNLOADING = re.compile(r"Downloading (.+?)(?: [\d.]+)? \(playwright ")
_PERCENT = re.compile(r"(\d{1,3})% of ([\d.]+ ?[KMG]i?B)")


class DownloadProgress:
    """Turns the installer's output into a short text, e.g. "Downloading Chromium… 40 % of 147.3 MiB"."""

    def __init__(self) -> None:
        self.browser = ""

    def feed(self, line: str) -> str | None:
        if match := _DOWNLOADING.search(line):
            self.browser = match.group(1)
            return f"Downloading {self.browser}…"
        if self.browser and (match := _PERCENT.search(line)):
            return f"Downloading {self.browser}… {match.group(1)} % of {match.group(2)}"
        return None


def install_browsers(quiet: bool = False, on_progress: Callable[[str], None] | None = None) -> None:
    """Downloads WebKit and Chromium into BROWSERS_DIR.

    Runs Playwright's own installer through its bundled Node driver, the same thing
    "python -m playwright install" does; unlike that command, it also works inside
    the packaged .exe, where there is no Python to run. "--no-shell" skips the
    headless-only Chromium build (about 270 MB) that this app never uses.

    quiet=True reads the installer's output instead of printing it (no console window)
    and reports the progress to `on_progress`. cancel_install() stops it from another
    thread; this function then raises InstallCancelledError.
    """
    global _installer, _installer_cancelled
    print(f"Downloading WebKit and Chromium into: {BROWSERS_DIR}")
    log.info("Downloading WebKit and Chromium into %s", BROWSERS_DIR)
    no_window = subprocess.CREATE_NO_WINDOW if quiet and sys.platform == "win32" else 0
    with _installer_lock:
        if _installer_cancelled:
            raise InstallCancelledError("The download of the browsers was cancelled.")
        try:
            process = subprocess.Popen(
                [*driver_command(), "install", "--no-shell", "webkit", "chromium"],
                env=os.environ.copy(),  # includes PLAYWRIGHT_BROWSERS_PATH
                stdout=subprocess.PIPE if quiet else None,
                stderr=subprocess.STDOUT if quiet else None,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=no_window,
            )
        except OSError as e:
            log.error("The browser installer could not run: %s", e)
            raise RuntimeError(f"The browsers could not be installed: {e}") from e
        _installer = process
    tail: deque[str] = deque(maxlen=12)  # the end of the output explains a failure
    progress = DownloadProgress()
    if process.stdout is not None:
        for line in process.stdout:
            tail.append(line.rstrip())
            if on_progress is not None and (text := progress.feed(line)) is not None:
                on_progress(text)
    returncode = process.wait()
    with _installer_lock:
        _installer = None
        cancelled = _installer_cancelled
    if cancelled:
        log.info("The download of the browsers was cancelled")
        raise InstallCancelledError("The download of the browsers was cancelled.")
    if returncode != 0:
        log.error("Browser installation failed (exit code %s) %s", returncode, "\n".join(tail))
        raise RuntimeError("The browsers could not be installed.")
    print("Browsers installed.")
    log.info("Browsers installed", extra={"event": "downloaded"})


def cancel_install() -> None:
    """Stops a running download of the browsers (the app is closing). The installer starts
    its own helper processes, so the whole process tree is ended; the next start resumes,
    since a download cut halfway is never counted as installed."""
    global _installer_cancelled
    with _installer_lock:
        _installer_cancelled = True
        process = _installer
    if process is None or process.poll() is not None:
        return
    log.info("Stopping the download of the browsers")
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            check=False,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        process.kill()


def playwright_devices(pw: Playwright) -> Devices:
    """Playwright's device descriptors (its own annotation leaves the types of the values out)."""
    return cast(Devices, pw.devices)  # pyright: ignore[reportUnknownMemberType]


def iphone_only(devices: Devices) -> Devices:
    """Only the portrait iPhones from Playwright's device list."""
    return {k: v for k, v in devices.items() if k.startswith("iPhone") and "landscape" not in k}


@lru_cache(maxsize=1)
def iphone_devices() -> Devices:
    """Playwright's iPhone list (starts Playwright only once)."""

    async def read() -> Devices:
        async with async_playwright() as pw:
            return iphone_only(playwright_devices(pw))

    return asyncio.run(read())


def screen_scale() -> float:
    """Windows display scale (1.0 = 100 %, 1.5 = 150 %)."""
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes

        percent: int = ctypes.windll.shcore.GetScaleFactorForDevice(0)
        return max(1.0, min(3.0, percent / 100)) if percent else 1.0
    except (AttributeError, OSError):
        return 1.0


def work_area() -> tuple[int, int, int, int]:
    """Usable area of the primary screen, without the taskbar: (left, top, width, height)
    in logical pixels, i.e. independent of the Windows display scale."""
    default = (0, 0, 1366, 728)
    if sys.platform != "win32":
        return default
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        # Read the area as a DPI-unaware thread, so it comes in logical pixels.
        previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-1))  # DPI_AWARENESS_CONTEXT_UNAWARE
        try:
            rect = wintypes.RECT()
            if not user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return default
        finally:
            if previous:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except (AttributeError, OSError):
        return default


def desktop_window_bounds() -> tuple[int, int, int, int]:
    """Size and position of the desktop-mode window: 85 % of the work area
    (up to DESKTOP_WINDOW_MAX), centered. Returns (width, height, left, top)."""
    left, top, area_width, area_height = work_area()
    width = min(DESKTOP_WINDOW_MAX[0], int(area_width * 0.85))
    height = min(DESKTOP_WINDOW_MAX[1], int(area_height * 0.85))
    return width, height, left + (area_width - width) // 2, top + (area_height - height) // 2


# ----------------------------------------------------------------------------
# File chooser
#
# WebKit on Windows does not open the system file chooser: clicking "upload"
# on a website does nothing. The request is intercepted, our own chooser is
# shown and the picked files are handed to the page.
# ----------------------------------------------------------------------------

MIME_EXTENSIONS = {
    "image/jpeg": ["jpg", "jpeg"],
    "image/png": ["png"],
    "image/heic": ["heic"],
    "image/heif": ["heif"],
    "image/webp": ["webp"],
    "image/gif": ["gif"],
    "video/mp4": ["mp4"],
    "video/quicktime": ["mov"],
    "video/webm": ["webm"],
    "video/x-m4v": ["m4v"],
}
MIME_GROUPS = {
    "image/*": ["jpg", "jpeg", "png", "heic", "heif", "webp", "gif"],
    "video/*": ["mp4", "mov", "m4v", "webm"],
}

_dialog_lock = threading.Lock()


def file_types(accept: str) -> list[tuple[str, str]]:
    """Chooser filters built from the <input> element's `accept` attribute."""
    extensions: list[str] = []
    for token in (t.strip().lower() for t in accept.split(",") if t.strip()):
        found = [token[1:]] if token.startswith(".") else MIME_GROUPS.get(token) or MIME_EXTENSIONS.get(token, [])
        extensions += [e for e in found if e not in extensions]
    types = [("All files", "*.*")]
    if extensions:
        types.insert(0, ("Supported files", " ".join(f"*.{e}" for e in extensions)))
    return types


def ask_files(multiple: bool, accept: str, parent: tk.Misc | None = None) -> list[str]:
    """Shows the Windows file chooser on top of every other window (command line only;
    the graphical interface uses its own Qt chooser)."""
    import tkinter as tk
    from tkinter import filedialog

    with _dialog_lock:
        owner = tk.Tk() if parent is None else tk.Toplevel(parent)
        owner.withdraw()
        # One of the overloads in tkinter's type stubs is untyped; this call is well-typed.
        owner.attributes("-topmost", True)  # pyright: ignore[reportUnknownMemberType]
        title = "Select files to upload"
        types = file_types(accept)
        try:
            if multiple:
                return list(filedialog.askopenfilenames(parent=owner, title=title, filetypes=types))
            path = filedialog.askopenfilename(parent=owner, title=title, filetypes=types)
            return [path] if path else []
        finally:
            owner.destroy()


async def _ask_files_in_thread(multiple: bool, accept: str) -> list[str]:
    return await asyncio.to_thread(ask_files, multiple, accept)


def ask_dialog(kind: str, message: str, default: str, site: str) -> tuple[bool, str]:
    """A page's alert, confirm, prompt or "leave site?" question, on top of every other
    window (command line only; the graphical interface shows its own)."""
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    with _dialog_lock:
        owner = tk.Tk()
        owner.withdraw()
        owner.attributes("-topmost", True)  # pyright: ignore[reportUnknownMemberType]
        title = f"{site} says"
        try:
            if kind == "alert":
                messagebox.showinfo(title, message, parent=owner)
                return True, ""
            if kind == "prompt":
                text = simpledialog.askstring(title, message, initialvalue=default, parent=owner)
                return text is not None, text or ""
            if kind == "beforeunload":
                question = "Leave this page? Changes you made may not be saved."
                return bool(messagebox.askokcancel("Leave site?", question, parent=owner)), ""
            return bool(messagebox.askokcancel(title, message, parent=owner)), ""
        finally:
            owner.destroy()


async def _ask_dialog_in_thread(kind: str, message: str, default: str, site: str) -> tuple[bool, str]:
    return await asyncio.to_thread(ask_dialog, kind, message, default, site)


# ----------------------------------------------------------------------------
# Opening profiles
# ----------------------------------------------------------------------------


def launch_options(profile: Profile, devices: Devices) -> dict[str, Any]:
    languages = stealth.languages(profile.locale, profile.engine, profile.mode)
    options: dict[str, Any] = {
        "headless": False,
        "locale": profile.locale,
        "timezone_id": profile.timezone,
        "color_scheme": "light" if profile.theme == "light" else "dark",
        "extra_http_headers": {"Accept-Language": stealth.accept_language(languages)},
        "accept_downloads": True,  # saved to the Downloads folder (see browser_session.py)
    }
    if profile.engine == "chromium" or profile.mode == "desktop":
        # Without Playwright's automation flag (navigator.webdriver, the automation bar)
        # and with the pop-up blocker on, like any Chrome.
        options["ignore_default_args"] = list(stealth.CHROMIUM_IGNORED_ARGS)
        options["args"] = list(stealth.CHROMIUM_ARGS)
    if profile.mode == "desktop":
        # A normal window (not maximized), centered; the page follows the window size.
        # Size and position are always passed, otherwise Chromium restores the last
        # placement saved in the profile, which may be maximized.
        width, height, left, top = desktop_window_bounds()
        options["no_viewport"] = True
        options["args"] += [f"--window-size={width},{height}", f"--window-position={left},{top}"]
        log.debug("Desktop window %dx%d at (%d, %d)", width, height, left, top)
    else:
        device = dict(devices[profile.device])
        device.pop("default_browser_type", None)
        if profile.engine == "webkit" and profile.quality == "smooth":
            # WebKit on Windows renders without the GPU: at x3 resolution it paints
            # 9 times more pixels and scrolling stutters. Use the real screen scale.
            device["device_scale_factor"] = screen_scale()
        options.update(device)
        options["screen"] = stealth.iphone_screen(device)  # the whole screen, not only the page area
        viewport: dict[str, int] = device.get("viewport", {})
        log.debug(
            "Emulating %s: %sx%s, scale %s",
            profile.device,
            viewport.get("width"),
            viewport.get("height"),
            device.get("device_scale_factor"),
        )
    if user_agent := stealth.user_agent_for(profile):
        options["user_agent"] = user_agent
    return options


def _first_line(error: Exception) -> str:
    text = str(error).strip()
    return text.splitlines()[0] if text else repr(error)


def print_notice(name: str, _state: str, message: str) -> None:
    print(f"[{name}] {message}")


# The session keeper of every open context: whoever closes a profile saves its session first.
_keepers: dict[BrowserContext, SessionKeeper] = {}


async def close_quietly(context: BrowserContext) -> None:
    keeper = _keepers.get(context)
    if keeper is not None:
        await keeper.close()
    with contextlib.suppress(PlaywrightError):
        await context.close()


async def run_profile(
    pw: Playwright,
    profile: Profile,
    devices: Devices,
    *,
    notify: Notify = print_notice,
    contexts: dict[str, BrowserContext] | None = None,
    choose_files: ChooseFiles | None = None,
    ask: AskDialog | None = None,
    restore_tabs: bool = True,
) -> None:
    """Opens a profile and waits until its window is closed.

    `contexts` (optional) registers the open context so it can be closed from
    outside. `choose_files` shows the file chooser when a page asks for one in
    WebKit, and `ask` the alerts, confirmations and prompts of the pages; by default
    they are opened in a separate thread. With `restore_tabs` the tabs open the last
    time are opened again instead of the start page.
    """
    choose = choose_files or _ask_files_in_thread
    ask_user = ask or _ask_dialog_in_thread
    name = profile.name
    log.info("Launching '%s' (%s)", name, describe(profile))
    browser_type = pw.webkit if profile.engine == "webkit" else pw.chromium
    device = devices.get(profile.device) if profile.mode == "iphone" else None
    try:
        profile.folder.mkdir(parents=True, exist_ok=True)
        if browser_type is pw.chromium:
            browser_session.mark_clean_exit(profile.folder)
        saved = browser_session.load_session(profile.folder)
        context = await browser_type.launch_persistent_context(str(profile.folder), **launch_options(profile, devices))
    except OSError as e:
        log.error("Could not create the data folder of '%s': %s", name, e)
        notify(name, "error", f"Could not create the profile's data folder: {e}")
        return
    except PlaywrightError as e:
        log.error("Could not launch '%s': %s", name, e)
        notify(name, "error", f"Could not open the profile (is it already open?): {_first_line(e)}")
        return

    # Registered first, so the close is not missed if the window closes right away.
    closed = asyncio.Event()
    context.on("close", lambda _context: closed.set())
    keeper = SessionKeeper(context, profile.folder, name)
    _keepers[context] = keeper
    pending: set[asyncio.Task[None]] = set()  # keeps references so the tasks are not lost

    def start(coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        pending.add(task)
        task.add_done_callback(pending.discard)

    def on_page_close(_page: Page) -> None:
        # Closing the last tab closes the whole profile.
        if not context.pages and not closed.is_set():
            log.debug("'%s': no tabs left, closing the profile", name)
            start(close_quietly(context))

    async def on_file_chooser(chooser: FileChooser) -> None:
        try:
            accept = await chooser.element.get_attribute("accept") or ""
            multiple = chooser.is_multiple()
            log.info("'%s': the page asked for files (accept=%s, multiple=%s)", name, accept or "*", multiple)
            paths = await choose(multiple, accept)
            if paths:
                await chooser.set_files(paths)
                log.info("'%s': delivered %d file(s): %s", name, len(paths), ", ".join(paths))
            else:
                log.info("'%s': file selection cancelled", name)
        except PlaywrightError as e:
            if not closed.is_set():
                log.warning("'%s': the files could not be delivered: %s", name, e)
                notify(name, "warning", f"The files could not be delivered: {_first_line(e)}")

    async def on_dialog(dialog: Dialog) -> None:
        """Without this, Playwright would dismiss every alert, confirm and prompt on its own."""
        site = safe_url(dialog.page.url if dialog.page else "")
        log.info("'%s': the page shows a %s dialog", name, dialog.type)
        try:
            accepted, text = await ask_user(dialog.type, dialog.message, dialog.default_value, site)
            if accepted:
                await dialog.accept(text) if dialog.type == "prompt" else await dialog.accept()
            else:
                await dialog.dismiss()
        except PlaywrightError as e:  # the page closed while the question was shown
            log.debug("'%s': the dialog was already gone: %s", name, e)

    async def on_download(download: Download) -> None:
        """Playwright would delete downloads when the window closes: keep a copy in Downloads."""
        try:
            folder = browser_session.downloads_folder()
            folder.mkdir(parents=True, exist_ok=True)
            path = browser_session.unique_path(folder, download.suggested_filename)
            await download.save_as(path)
        except (PlaywrightError, OSError) as e:
            if not closed.is_set():
                log.warning("'%s': the download could not be saved: %s", name, e)
                notify(name, "warning", f"The download could not be saved: {_first_line(e)}")
            return
        log.info("'%s': downloaded %s", name, path, extra={"event": "saved"})
        notify(name, "saved", path.name)

    def prepare_page(page: Page) -> None:
        log.debug("'%s': new tab", name)

        def on_navigated(frame: Frame) -> None:
            if frame == page.main_frame:
                log.debug("'%s': navigated to %s", name, safe_url(frame.url))

        def on_crash(_page: Page) -> None:
            log.error("'%s': the page crashed (%s)", name, safe_url(page.url))

        def on_page_error(error: Exception) -> None:
            # Websites throw many harmless JavaScript errors, so they are only debug entries.
            log.debug("'%s': JavaScript error on the page: %s", name, error)

        page.on("close", on_page_close)
        page.on("framenavigated", on_navigated)
        page.on("crash", on_crash)
        page.on("pageerror", on_page_error)
        page.on("dialog", lambda dialog: start(on_dialog(dialog)))
        page.on("download", lambda download: start(on_download(download)))
        keeper.track(page)
        if profile.engine == "webkit":
            page.on("filechooser", on_file_chooser)

    try:
        if contexts is not None:
            contexts[name] = context
        context.on("page", prepare_page)
        for page in context.pages:
            prepare_page(page)
        label = profile.device if profile.mode == "iphone" else "desktop"
        log.info("'%s' is open as %s", name, label, extra={"event": "opened"})
        notify(name, "opened", f"Opened as {label} ({profile.engine}).")
        try:
            await context.add_init_script(stealth.init_script(profile, device))
            await keeper.restore(saved)
            page = context.pages[0] if context.pages else await context.new_page()
            if page.url in ("", "about:blank"):
                tabs = saved.tabs if restore_tabs and saved.tabs else [profile.start_url]
                if tabs != [profile.start_url]:
                    log.info("'%s': reopening %d tab(s) from the last session", name, len(tabs))
                for url in tabs[1:]:  # the others load in the background
                    start(_open_tab(context, url, name))
                await page.goto(tabs[0], wait_until="domcontentloaded", timeout=60_000)
                log.info("'%s' loaded %s", name, safe_url(tabs[0]))
        except PlaywrightError as e:
            if not closed.is_set():
                log.warning("'%s': problem loading the page: %s", name, e)
                notify(name, "warning", f"Problem loading the page: {_first_line(e)}")
        await closed.wait()
    finally:
        await close_quietly(context)
        _keepers.pop(context, None)
        if contexts is not None:
            contexts.pop(name, None)
        log.info("'%s' closed, session saved", name, extra={"event": "closed"})
        notify(name, "closed", "Closed. Session saved.")


async def _open_tab(context: BrowserContext, url: str, name: str) -> None:
    try:
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PlaywrightError as e:
        log.debug("'%s': could not reopen %s: %s", name, safe_url(url), e)


async def _run_many(profiles: list[Profile]) -> None:
    async with async_playwright() as pw:
        devices = playwright_devices(pw)
        runnable: list[Profile] = []
        for profile in profiles:
            if profile.mode == "iphone" and profile.device not in devices:
                log.warning("'%s': unknown device '%s'; skipped", profile.name, profile.device)
                print(f"[{profile.name}] Unknown device '{profile.device}'; skipped.")
            else:
                runnable.append(profile)
        if runnable:
            print("Close each window when you are done (or press Ctrl+C to close them all).")
            await asyncio.gather(*(run_profile(pw, p, devices) for p in runnable))


def open_profiles(profiles: list[Profile]) -> None:
    """Opens several profiles at once and waits until they are closed (command line)."""
    if not browsers_installed():
        install_browsers()
    try:
        asyncio.run(_run_many(profiles))
    except KeyboardInterrupt:
        log.info("Interrupted from the keyboard, closing all profiles")
        print("\nClosing all profiles...")
