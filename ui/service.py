"""
BrowserService: keeps Playwright in its own thread with its own asyncio loop, so
the interface never freezes. It reports back through Qt signals, which Qt
delivers safely on the interface thread.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import threading
from typing import Any

from playwright.async_api import BrowserContext, Playwright, async_playwright
from PySide6.QtCore import QObject, Signal

import launcher
from profiles import Devices, Profile

log = logging.getLogger("app.gui")

# Future the interface resolves with the files picked in the chooser.
FilesFuture = concurrent.futures.Future[list[str]]


class ServiceSignals(QObject):
    ready = Signal()
    installed = Signal()  # the browsers finished downloading (first run)
    info = Signal(str)
    failed = Signal(str)
    state = Signal(str, str, str)  # profile name, state ("opened", "warning", "error", "closed", "done"), message
    files = Signal(bool, str, object)  # multiple, accept, FilesFuture


class BrowserService:
    """Opens and closes profiles on demand from its own thread."""

    def __init__(self) -> None:
        self.signals = ServiceSignals()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="browser-service", daemon=True)
        self.ready = threading.Event()
        self.pw: Playwright | None = None
        self.devices: Devices = {}
        self.contexts: dict[str, BrowserContext] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    # --- service thread -----------------------------------------------------
    def start(self) -> None:
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.set_exception_handler(self._on_loop_exception)
        try:
            if not launcher.browsers_installed():
                self.signals.info.emit("Downloading browsers (first run only)…")
                launcher.install_browsers(quiet=True)
                self.signals.installed.emit()
            self.loop.run_until_complete(self._start_playwright())
        except Exception as e:  # shown in the interface
            log.exception("The browser engine could not start")
            self.signals.failed.emit(f"The browser engine could not start: {e}")
            self.loop.close()
            return
        self.ready.set()
        self.signals.ready.emit()
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    async def _start_playwright(self) -> None:
        log.info("Starting the browser engine")
        self.pw = await async_playwright().start()
        self.devices = launcher.playwright_devices(self.pw)
        log.info("Browser engine ready (%d devices available)", len(self.devices))

    @staticmethod
    def _on_loop_exception(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        # Errors in background tasks that nobody awaited would otherwise go unnoticed.
        log.error("Background error: %s", context.get("message"), exc_info=context.get("exception"))

    def _notify(self, name: str, state: str, message: str) -> None:
        self.signals.state.emit(name, state, message)

    # --- calls from the interface thread ------------------------------------
    def open(self, profile: Profile) -> None:
        asyncio.run_coroutine_threadsafe(self._open(profile), self.loop)

    def close(self, name: str) -> None:
        asyncio.run_coroutine_threadsafe(self._close(name), self.loop)

    def stop(self) -> None:
        """Closes every profile (saving its session) and stops the thread. Blocks: call it off the UI thread."""
        if not self.ready.is_set():
            return
        log.info("Stopping the browser engine")
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
        with contextlib.suppress(Exception):  # the app is shutting down
            future.result(timeout=20)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)

    # --- coroutines (service thread) ----------------------------------------
    async def _open(self, profile: Profile) -> None:
        if profile.name not in self.tasks:
            self.tasks[profile.name] = asyncio.create_task(self._run_profile(profile))

    async def _choose_files(self, multiple: bool, accept: str) -> list[str]:
        # The chooser is shown from the interface thread; here we only wait for the answer.
        future: FilesFuture = concurrent.futures.Future()
        self.signals.files.emit(multiple, accept, future)
        return await asyncio.wrap_future(future)

    async def _run_profile(self, profile: Profile) -> None:
        try:
            if self.pw is None:
                log.error("Could not open '%s': the browser engine is not running", profile.name)
                self._notify(profile.name, "error", "The browser engine is not running.")
                return
            if profile.mode == "iphone" and profile.device not in self.devices:
                log.error("Could not open '%s': unknown device '%s'", profile.name, profile.device)
                self._notify(profile.name, "error", f"Unknown device '{profile.device}'.")
                return
            await launcher.run_profile(
                self.pw,
                profile,
                self.devices,
                notify=self._notify,
                contexts=self.contexts,
                choose_files=self._choose_files,
            )
        except Exception as e:  # shown in the interface
            log.exception("Unexpected error in '%s'", profile.name)
            self._notify(profile.name, "error", f"Unexpected error: {e}")
        finally:
            self.tasks.pop(profile.name, None)
            self._notify(profile.name, "done", "")

    async def _close(self, name: str) -> None:
        if (context := self.contexts.get(name)) is not None:
            await launcher.close_quietly(context)

    async def _shutdown(self) -> None:
        for context in list(self.contexts.values()):
            await launcher.close_quietly(context)
        if self.tasks:
            await asyncio.wait(list(self.tasks.values()), timeout=10)
        if self.pw is not None:
            await self.pw.stop()
