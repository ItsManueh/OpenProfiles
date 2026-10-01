"""
AppController: the state of the app (profiles and whether each one is open) and
every action on it. Views only display this state and call these methods, so
the logic does not depend on how the interface looks.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

import backup
import launcher
import profiles as storage
from profiles import Devices, Profile, ProfileError, create_profile, load_profiles, update_profile
from ui.service import BrowserService

log = logging.getLogger("app.gui")

# Profile states shown by the interface; a profile missing from `states` is closed.
CLOSED, OPENING, OPENED, CLOSING = "closed", "opening", "opened", "closing"


class AppController(QObject):
    profiles_changed = Signal()  # the list of profiles must be redrawn
    state_changed = Signal(str, str)  # profile name, new state
    ready_changed = Signal(bool)  # the browser engine is (not) ready
    message = Signal(str, bool)  # progress text for the status bar, or an error (shown as a notification)
    notified = Signal(str, str, str)  # event kind (created, opened, error...), profile name, detail
    files_requested = Signal(bool, str, object)  # multiple, accept, future to resolve with the paths
    dialog_requested = Signal(str, str, str, str, object)  # a page's alert or question (see PageDialog)
    retry_available = Signal(bool)  # the browser engine could not start: it can be started again
    backup_finished = Signal(str, str, str)  # kind of notification, title, detail (from a worker thread)
    shutdown_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.ready = False
        self.states: dict[str, str] = {}
        self.closing = False
        self._load_error = ""  # last error reading profiles.json, reported once
        self.restore_tabs = True  # reopen the tabs of the last session (a setting)
        self._deleted: dict[str, storage.DeletedProfile] = {}  # deleted profiles that can still be restored
        self.backup_running = False
        self.service = self._create_service()

    def _create_service(self) -> BrowserService:
        service = BrowserService()
        signals = service.signals
        signals.ready.connect(self._on_ready)
        signals.installed.connect(self._on_installed)
        signals.info.connect(self._on_info)
        signals.failed.connect(self._on_failed)
        signals.state.connect(self._on_state)
        signals.files.connect(self.files_requested)
        signals.dialog.connect(self.dialog_requested)
        service.restore_tabs = self.restore_tabs
        return service

    def set_restore_tabs(self, enabled: bool) -> None:
        """Applies to the profiles opened from now on."""
        self.restore_tabs = enabled
        self.service.restore_tabs = enabled

    def start(self) -> None:
        """Starts the browser engine; after a failure (no connection on the first download,
        for example) it starts a new one."""
        if self.ready or self.closing or self.service.thread.is_alive():
            return
        if self.service.thread.ident is not None:  # this one already ran and failed
            log.info("Starting the browser engine again")
            self.service = self._create_service()
        else:  # first start: what an earlier session left in the trash is deleted for good
            threading.Thread(target=storage.empty_trash, name="empty-trash", daemon=True).start()
        self.retry_available.emit(False)
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
            profiles = load_profiles()
        except ProfileError as e:
            # The list is redrawn often; the same problem is reported only once.
            if str(e) != self._load_error:
                self._load_error = str(e)
                self.message.emit(str(e), True)
            return {}
        self._load_error = ""
        return profiles

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
        # The details of the notifications never include the name of the profile.
        if original_name is None:
            self.notified.emit("created", saved.name, saved.device if saved.mode == "iphone" else "Desktop")
        elif original_name != saved.name:
            self.notified.emit("edited", saved.name, "Renamed")
        else:
            self.notified.emit("edited", saved.name, "Applied the next time it opens")
        return saved

    def delete_profile(self, name: str) -> None:
        """Deletes a profile; undo_delete() brings it back until finish_delete() is called (the
        notification with "Undo" closes), which deletes its data for good."""
        if name in self.states:
            log.warning("Tried to delete '%s' while it is open", name)
            self.notified.emit("warning", name, "Close the profile before deleting it.")
            return
        try:
            self._deleted[name] = storage.delete_profile(name, undoable=True)
        except (ProfileError, OSError) as e:
            log.warning("Could not delete '%s': %s", name, e)
            self.notified.emit("error", name, "Could not delete the profile. The details are in the logs.")
            return
        self.profiles_changed.emit()
        self.notified.emit("deleted", name, "With its session and data")

    def undo_delete(self, name: str) -> None:
        deleted = self._deleted.pop(name, None)
        if deleted is None:
            return
        try:
            storage.restore_profile(deleted)
        except ProfileError as e:
            log.warning("Could not restore '%s': %s", name, e)
            self.notified.emit("error", name, "Could not restore the profile. The details are in the logs.")
            storage.purge(deleted)
            return
        self.profiles_changed.emit()
        self.notified.emit("restored", name, "With its session and data")

    def finish_delete(self, name: str) -> None:
        """The deletion can no longer be undone: its data is deleted for good (in the background)."""
        deleted = self._deleted.pop(name, None)
        if deleted is not None:
            threading.Thread(target=storage.purge, args=(deleted,), name="purge", daemon=True).start()

    def duplicate_profile(self, name: str) -> None:
        """A copy of the settings, with its own fingerprint and no session."""
        if not self.ready:
            return
        try:
            copy = storage.duplicate_profile(name, self.iphones)
        except (ProfileError, OSError) as e:
            log.warning("Could not duplicate '%s': %s", name, e)
            self.notified.emit("error", name, "Could not duplicate the profile. The details are in the logs.")
            return
        log.info("Duplicated '%s' as '%s'", name, copy.name)
        self.profiles_changed.emit()
        self.notified.emit("created", copy.name, "Duplicate with its own fingerprint")

    # --- backups ------------------------------------------------------------
    def export_profiles(self, path: str) -> None:
        """Writes every profile to a .zip in the background; they must be closed."""
        if self.backup_running:
            return
        if self.states:
            self.notified.emit("warning", "", "Close the profiles before exporting them.")
            return
        self._run_backup(lambda: self._export(path))

    def import_profiles(self, path: str) -> None:
        if not self.backup_running:
            self._run_backup(lambda: self._import(path))

    def _run_backup(self, work: Callable[[], None]) -> None:
        self.backup_running = True
        self.message.emit("Working on the backup…", False)

        def run() -> None:
            try:
                work()
            finally:
                self.backup_running = False

        threading.Thread(target=run, name="backup", daemon=True).start()

    def _export(self, path: str) -> None:
        try:
            count, size = backup.export_profiles(Path(path))
        except (ProfileError, OSError) as e:
            log.error("Could not export the profiles: %s", e)
            self.backup_finished.emit("error", "Could not export the profiles", str(e))
            return
        megabytes = f"{size / 1_048_576:.1f} MB"
        self.backup_finished.emit(
            "saved", "Profiles exported", f"{count} profile{'' if count == 1 else 's'} · {megabytes}"
        )

    def _import(self, path: str) -> None:
        try:
            names = backup.import_profiles(Path(path))
        except (ProfileError, OSError) as e:
            log.error("Could not import the profiles: %s", e)
            self.backup_finished.emit("error", "Could not import the profiles", str(e))
            return
        count = len(names)
        self.backup_finished.emit("created", "Profiles imported", f"{count} profile{'' if count == 1 else 's'} added")

    def reorder(self, names: list[str]) -> None:
        """Saves the order of the list (after dragging a profile); the list already shows it."""
        try:
            storage.reorder_profiles(names)
        except (ProfileError, OSError) as e:
            log.warning("Could not save the order of the profiles: %s", e)
            self.notified.emit("error", "", "Could not save the order of the profiles.")
            self.profiles_changed.emit()  # back to the saved order

    # --- opening and closing ------------------------------------------------
    def open_profile(self, name: str) -> None:
        if (profile := self.profiles().get(name)) is not None:
            self._open(profile)

    def _open(self, profile: Profile) -> None:
        if not self.ready or profile.name in self.states:
            return
        log.info("Open requested for '%s'", profile.name)
        self._set_state(profile.name, OPENING)
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
        # Nothing new may open while the profiles close; the interface disables its buttons.
        self.ready = False
        self.ready_changed.emit(False)
        self.retry_available.emit(False)
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

    def _on_installed(self) -> None:
        self.notified.emit("downloaded", "", "WebKit and Chromium are ready to use")

    def _on_info(self, text: str) -> None:
        self.message.emit(text, False)

    def _on_failed(self, text: str) -> None:
        if self.closing:
            return
        self.message.emit("The browsers could not start.", False)  # replaces "Preparing browsers…"
        self.message.emit(text, True)
        self.retry_available.emit(True)

    def _on_ready(self) -> None:
        self.ready = True
        self.ready_changed.emit(True)
        self.profiles_changed.emit()  # redraws the list with the buttons enabled
        self.message.emit("", False)  # clears "Preparing browsers…"

    def _on_state(self, name: str, state: str, text: str) -> None:
        if state == "opened":
            self._set_state(name, OPENED)
            self.notified.emit("opened", name, "")
            try:
                storage.mark_opened(name)
            except (ProfileError, OSError) as e:
                log.debug("Could not save when '%s' was opened: %s", name, e)
            else:
                self.profiles_changed.emit()  # its card shows when it was last opened
        elif state == "saved":  # a download, kept in the Downloads folder
            self.notified.emit("file", name, text)
        elif state in ("error", "warning"):
            if not self.closing:  # a page cut short because the app is closing is not a problem
                self.notified.emit(state, name, text)
        elif state == "closed":
            if not self.closing:  # quitting the app closes them all; no need to announce each one
                self.notified.emit("closed", name, "Session saved")
        elif state == "done":
            self._set_state(name, CLOSED)
