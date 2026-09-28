"""
Opening profiles with Playwright: browser installation, launch options for each
mode, file chooser and the lifecycle of each window. Several profiles can be
open at the same time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import playwright
from playwright.async_api import BrowserContext, FileChooser, Frame, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from profiles import BROWSERS_DIR, Devices, Profile, describe

if TYPE_CHECKING:
    import tkinter as tk

log = logging.getLogger("app.launcher")

# notify(profile_name, state, message) with state "opened", "warning", "error" or "closed".
Notify = Callable[[str, str, str], None]
# choose_files(multiple, accept) -> paths picked by the user.
ChooseFiles = Callable[[bool, str], Awaitable[list[str]]]

# WebKit on Windows reports no touch points even when emulating a phone;
# a real iPhone reports 5.
TOUCH_POINTS_SCRIPT = (
    "Object.defineProperty(Navigator.prototype, 'maxTouchPoints', {get: () => 5, configurable: true});"
)
# Largest size of the desktop-mode window (it never covers the whole screen).
DESKTOP_WINDOW_MAX = (1440, 900)


# ----------------------------------------------------------------------------
# Browsers, devices and screen
# ----------------------------------------------------------------------------


def browsers_installed() -> bool:
    return any(BROWSERS_DIR.glob("webkit-*")) and any(BROWSERS_DIR.glob("chromium-*"))


def driver_command() -> list[str]:
    """Node and CLI script of the driver bundled with Playwright (also inside the .exe)."""
    driver = Path(playwright.__file__).parent / "driver"
    node = driver / ("node.exe" if sys.platform == "win32" else "node")
    return [str(node), str(driver / "package" / "cli.js")]


def install_browsers(quiet: bool = False) -> None:
    """Downloads WebKit and Chromium into BROWSERS_DIR.

    Runs Playwright's own installer through its bundled Node driver, the same thing
    "python -m playwright install" does; unlike that command, it also works inside
    the packaged .exe, where there is no Python to run. "--no-shell" skips the
    headless-only Chromium build (about 270 MB) that this app never uses.
    """
    print(f"Downloading WebKit and Chromium into: {BROWSERS_DIR}")
    log.info("Downloading WebKit and Chromium into %s", BROWSERS_DIR)
    no_window = subprocess.CREATE_NO_WINDOW if quiet and sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            [*driver_command(), "install", "--no-shell", "webkit", "chromium"],
            env=os.environ.copy(),  # includes PLAYWRIGHT_BROWSERS_PATH
            check=False,
            capture_output=quiet,  # quiet: no output and no console window
            text=True,
            errors="replace",
            creationflags=no_window,
        )
    except OSError as e:
        log.error("The browser installer could not run: %s", e)
        raise RuntimeError(f"The browsers could not be installed: {e}") from e
    if result.returncode != 0:
        # With quiet=True the output is captured, so its end goes to the log to explain the failure.
        details = (result.stderr or result.stdout or "").strip()[-800:] if quiet else ""
        log.error("Browser installation failed (exit code %s) %s", result.returncode, details)
        raise RuntimeError("The browsers could not be installed.")
    print("Browsers installed.")
    log.info("Browsers installed")


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


# ----------------------------------------------------------------------------
# Opening profiles
# ----------------------------------------------------------------------------


def launch_options(profile: Profile, devices: Devices) -> dict[str, Any]:
    options: dict[str, Any] = {
        "headless": False,
        "locale": profile.locale,
        "timezone_id": profile.timezone,
        "color_scheme": "light" if profile.theme == "light" else "dark",
    }
    if profile.mode == "desktop":
        # A normal window (not maximized), centered; the page follows the window size.
        # Size and position are always passed, otherwise Chromium restores the last
        # placement saved in the profile, which may be maximized.
        width, height, left, top = desktop_window_bounds()
        options["no_viewport"] = True
        options["args"] = [f"--window-size={width},{height}", f"--window-position={left},{top}"]
        log.debug("Desktop window %dx%d at (%d, %d)", width, height, left, top)
    else:
        device = dict(devices[profile.device])
        device.pop("default_browser_type", None)
        if profile.engine == "webkit" and profile.quality == "smooth":
            # WebKit on Windows renders without the GPU: at x3 resolution it paints
            # 9 times more pixels and scrolling stutters. Use the real screen scale.
            device["device_scale_factor"] = screen_scale()
        options.update(device)
        viewport: dict[str, int] = device.get("viewport", {})
        log.debug(
            "Emulating %s: %sx%s, scale %s",
            profile.device,
            viewport.get("width"),
            viewport.get("height"),
            device.get("device_scale_factor"),
        )
    if profile.user_agent:
        options["user_agent"] = profile.user_agent
    return options


def _first_line(error: Exception) -> str:
    text = str(error).strip()
    return text.splitlines()[0] if text else repr(error)


def print_notice(name: str, _state: str, message: str) -> None:
    print(f"[{name}] {message}")


async def close_quietly(context: BrowserContext) -> None:
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
) -> None:
    """Opens a profile and waits until its window is closed.

    `contexts` (optional) registers the open context so it can be closed from
    outside. `choose_files` shows the file chooser when a page asks for one in
    WebKit; by default one is opened in a separate thread.
    """
    choose = choose_files or _ask_files_in_thread
    name = profile.name
    log.info("Launching '%s' (%s)", name, describe(profile))
    profile.folder.mkdir(parents=True, exist_ok=True)
    browser_type = pw.webkit if profile.engine == "webkit" else pw.chromium
    try:
        context = await browser_type.launch_persistent_context(str(profile.folder), **launch_options(profile, devices))
    except PlaywrightError as e:
        log.error("Could not launch '%s': %s", name, e)
        notify(name, "error", f"Could not open it (is it already open?): {_first_line(e)}")
        return

    # Registered first, so the close is not missed if the window closes right away.
    closed = asyncio.Event()
    context.on("close", lambda _context: closed.set())
    pending: set[asyncio.Task[None]] = set()  # keeps references so the tasks are not lost

    def on_page_close(_page: Page) -> None:
        # Closing the last tab closes the whole profile.
        if not context.pages and not closed.is_set():
            log.debug("'%s': no tabs left, closing the profile", name)
            task = asyncio.ensure_future(close_quietly(context))
            pending.add(task)
            task.add_done_callback(pending.discard)

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

    def prepare_page(page: Page) -> None:
        log.debug("'%s': new tab", name)

        def on_navigated(frame: Frame) -> None:
            if frame == page.main_frame:
                log.debug("'%s': navigated to %s", name, frame.url)

        def on_crash(_page: Page) -> None:
            log.error("'%s': the page crashed (%s)", name, page.url)

        def on_page_error(error: Exception) -> None:
            # Websites throw many harmless JavaScript errors, so they are only debug entries.
            log.debug("'%s': JavaScript error on the page: %s", name, error)

        page.on("close", on_page_close)
        page.on("framenavigated", on_navigated)
        page.on("crash", on_crash)
        page.on("pageerror", on_page_error)
        if profile.engine == "webkit":
            page.on("filechooser", on_file_chooser)

    try:
        if contexts is not None:
            contexts[name] = context
        context.on("page", prepare_page)
        for page in context.pages:
            prepare_page(page)
        label = profile.device if profile.mode == "iphone" else "desktop"
        log.info("'%s' is open as %s", name, label)
        notify(name, "opened", f"Opened as {label} ({profile.engine}).")
        try:
            if profile.mode == "iphone":
                await context.add_init_script(TOUCH_POINTS_SCRIPT)
            page = context.pages[0] if context.pages else await context.new_page()
            if page.url in ("", "about:blank"):
                await page.goto(profile.start_url, wait_until="domcontentloaded", timeout=60_000)
                log.info("'%s' loaded %s", name, profile.start_url)
        except PlaywrightError as e:
            if not closed.is_set():
                log.warning("'%s': problem loading %s: %s", name, profile.start_url, e)
                notify(name, "warning", f"Problem loading {profile.start_url}: {_first_line(e)}")
        await closed.wait()
    finally:
        await close_quietly(context)
        if contexts is not None:
            contexts.pop(name, None)
        log.info("'%s' closed, session saved", name)
        notify(name, "closed", "Closed. Session saved.")


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
