"""
AppController: the state of the app (profiles and whether each one is open) and
every action on it. Views only display this state and call these methods, so
the logic does not depend on how the interface looks.
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

import launcher
from profiles import Devices, Profile, ProfileError, create_profile, delete_profile, load_profiles, update_profile
from ui.service import BrowserService

log = logging.getLogger("app.gui")

# Profile states shown by the interface; a profile missing from `states` is closed.
CLOSED, OPENING, OPENED, CLOSING = "closed", "opening", "opened", "closing"


class AppController(QObject):
    profiles_changed = Signal()  # the list of profiles must be redrawn
    state_changed = Signal(str, str)  # profile name, new state
    ready_changed = Signal(bool)  # the browser engine is (not) ready
    message = Signal(str, bool)  # text for the status bar, is it an error
    files_requested = Signal(bool, str, object)  # multiple, accept, future to resolve with the paths
    shutdown_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.service = BrowserService()
        self.ready = False
        self.states: dict[str, str] = {}
        self.closing = False
        signals = self.service.signals
        signals.ready.connect(self._on_ready)
        signals.info.connect(self._on_info)
        signals.failed.connect(self._on_failed)
        signals.state.connect(self._on_state)
        signals.files.connect(self.files_requested)

    def start(self) -> None:
        self.message.emit("Preparing browsers…", False)
        self.service.start()

    # --- queries ------------------------------------------------------------
    @property
    def devices(self) -> Devices:
        return self.service.devices

    @property
    def iphones(self) -> Devices:
        return launcher.iphone_only(self.service.devices)

    def profiles(self) -> dict[str, Profile]:
        try:
            return load_profiles()
        except ProfileError as e:
            self.message.emit(str(e), True)
            return {}

    def state_of(self, name: str) -> str:
        return self.states.get(name, CLOSED)

    def open_count(self) -> int:
        return sum(1 for state in self.states.values() if state == OPENED)

    # --- profile data -------------------------------------------------------
    def save_profile(self, profile: Profile, original_name: str | None = None) -> Profile:
        """Creates (original_name=None) or updates a profile. Raises ProfileError."""
        try:
            if original_name is None:
                saved = create_profile(profile, self.iphones)
            else:
                saved = update_profile(original_name, profile, self.iphones)
        except ProfileError:
            raise  # invalid input (a taken name, for example): the dialog already shows it
        except OSError as e:
            log.warning("Could not save profile '%s': %s", profile.name, e)
            raise ProfileError(str(e)) from e
        self.profiles_changed.emit()
        if original_name is None:
            label = saved.device if saved.mode == "iphone" else "a desktop"
            self.message.emit(f'Profile "{saved.name}" created as {label}.', False)
        else:
            self.message.emit(f'Changes saved to "{saved.name}".', False)
        return saved

    def delete_profile(self, name: str) -> None:
        if name in self.states:
            log.warning("Tried to delete '%s' while it is open", name)
            self.message.emit("Close the profile before deleting it.", True)
            return
        try:
            delete_profile(name)
        except ProfileError as e:
            self.message.emit(str(e), True)
            return
        self.profiles_changed.emit()
        self.message.emit(f'Profile "{name}" deleted.', False)

    # --- opening and closing ------------------------------------------------
    def open_profile(self, name: str) -> None:
        if (profile := self.profiles().get(name)) is not None:
            self._open(profile)

    def _open(self, profile: Profile) -> None:
        if not self.ready or profile.name in self.states:
            return
        log.info("Open requested for '%s'", profile.name)
        self._set_state(profile.name, OPENING)
        self.message.emit(f'Opening "{profile.name}"…', False)
        self.service.open(profile)

    def close_profile(self, name: str) -> None:
        if self.states.get(name) == OPENED:
            log.info("Close requested for '%s'", name)
            self._set_state(name, CLOSING)
            self.service.close(name)

    def open_all(self) -> None:
        log.info("Open all requested")
        for profile in self.profiles().values():  # read the file once, not once per profile
            self._open(profile)

    def close_all(self) -> None:
        log.info("Close all requested")
        for name in list(self.states):
            self.close_profile(name)

    # --- shutdown -----------------------------------------------------------
    def shutdown(self) -> None:
        """Closes every profile (saving its session) and emits shutdown_finished."""
        if self.closing:
            return
        self.closing = True
        log.info("Closing the app (%d profile(s) open)", self.open_count())
        self.message.emit("Closing profiles and saving sessions…", False)

        def stop() -> None:
            self.service.stop()
            log.info("Session finished")
            self.shutdown_finished.emit()  # delivered on the interface thread

        threading.Thread(target=stop, name="shutdown", daemon=True).start()

    # --- service events -----------------------------------------------------
    def _set_state(self, name: str, state: str) -> None:
        if state == CLOSED:
            self.states.pop(name, None)
        else:
            self.states[name] = state
        self.state_changed.emit(name, state)

    def _on_info(self, text: str) -> None:
        self.message.emit(text, False)

    def _on_failed(self, text: str) -> None:
        self.message.emit(text, True)

    def _on_ready(self) -> None:
        self.ready = True
        self.ready_changed.emit(True)
        self.profiles_changed.emit()  # redraws the list with the buttons enabled
        self.message.emit("Ready.", False)

    def _on_state(self, name: str, state: str, text: str) -> None:
        if state == "opened":
            self._set_state(name, OPENED)
            self.message.emit(f'"{name}" is open.', False)
        elif state in ("error", "warning"):
            self.message.emit(f'"{name}": {text}', True)
        elif state == "closed":
            self.message.emit(f'"{name}": {text}', False)
        elif state == "done":
            self._set_state(name, CLOSED)
