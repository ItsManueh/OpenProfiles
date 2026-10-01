"""
Profile model, on-disk storage and user-agent generation.

Each profile has its own data folder (cookies, session, cache) and one of two modes:

- desktop (default): full browser without emulation, always Chromium.
- iphone: emulates an iPhone (screen, touch and Safari for iOS user-agent).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import re
import secrets
import shutil
import sys
import time
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

# Packaged as a single .exe (PyInstaller), the code runs from a temporary folder that
# is deleted on exit, so bundled files and the files the app writes live apart.
FROZEN = bool(getattr(sys, "frozen", False))
# Read-only files shipped with the app (inside the .exe when packaged).
RESOURCES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# Packaged, everything the app writes goes to %LOCALAPPDATA%\OpenProfiles, the standard
# place for Windows apps (not synced with the Microsoft account, unlike Roaming). From the
# source code it stays inside the project.
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
DATA_DIR = LOCAL_APP_DATA / "OpenProfiles" if FROZEN else RESOURCES_DIR / "data"
BROWSERS_DIR = DATA_DIR / "browsers" if FROZEN else RESOURCES_DIR / "browsers"
# Older builds kept the data in a folder next to the .exe; migrate_legacy_data() moves it.
LEGACY_DATA_DIR = Path(sys.executable).resolve().parent / "OpenProfiles-data" if FROZEN else None
PROFILES_DIR = DATA_DIR / "profiles"
PROFILES_FILE = DATA_DIR / "profiles.json"
FONTS_DIR = RESOURCES_DIR / "fonts"
ICON_FILE = RESOURCES_DIR / "assets" / "icon.ico"

# Browsers are downloaded inside the project: the system browsers are never used.
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(BROWSERS_DIR)

import playwright  # noqa: E402  (after setting the browsers path)

log = logging.getLogger("app.profiles")

# Playwright device descriptors: name -> settings (viewport, user_agent, device_scale_factor…).
Device = dict[str, Any]
Devices = dict[str, Device]

START_URL = "https://www.instagram.com/"
ENGINES = ("webkit", "chromium")
THEMES = ("dark", "light")
MODES = ("desktop", "iphone")
QUALITIES = ("smooth", "sharp")
COLORS = ("", "red", "orange", "yellow", "green", "blue", "purple", "pink")  # label of the card; "" = none
NOTES_MAX = 300

# Models picked at random, with the iOS version each one shipped with.
IPHONE_MODELS: dict[str, tuple[int, int]] = {
    "iPhone 12": (14, 1),
    "iPhone 12 Mini": (14, 2),
    "iPhone 12 Pro": (14, 1),
    "iPhone 12 Pro Max": (14, 2),
    "iPhone SE (3rd gen)": (15, 4),
    "iPhone 13": (15, 0),
    "iPhone 13 Mini": (15, 0),
    "iPhone 13 Pro": (15, 0),
    "iPhone 13 Pro Max": (15, 0),
    "iPhone 14": (16, 0),
    "iPhone 14 Plus": (16, 0),
    "iPhone 14 Pro": (16, 0),
    "iPhone 14 Pro Max": (16, 0),
    "iPhone 15": (17, 0),
    "iPhone 15 Plus": (17, 0),
    "iPhone 15 Pro": (17, 0),
    "iPhone 15 Pro Max": (17, 0),
    "iPhone 16": (18, 0),
    "iPhone 16 Plus": (18, 0),
    "iPhone 16 Pro": (18, 0),
    "iPhone 16 Pro Max": (18, 0),
    "iPhone 16e": (18, 3),
    "iPhone 17": (26, 0),
    "iPhone Air": (26, 0),
    "iPhone 17 Pro": (26, 0),
    "iPhone 17 Pro Max": (26, 0),
}

# Released iOS versions. Since iOS 26, Safari freezes the system version in the
# user-agent at "18_6" and only reports the real one in "Version/".
IOS_VERSIONS: list[tuple[int, int]] = [
    (16, 5), (16, 6), (16, 7),
    (17, 4), (17, 5), (17, 6), (17, 7),
    (18, 1), (18, 2), (18, 3), (18, 4), (18, 5), (18, 6),
    (26, 0), (26, 1), (26, 2),
]  # fmt: skip

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
URL_PATTERN = re.compile(r"^https?://\S+$")
# Device names Windows reserves: a folder with one of these names is not a normal folder.
RESERVED_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class ProfileError(ValueError):
    """Validation or storage error, with a message meant for the user."""


@dataclass
class Profile:
    name: str
    device: str  # iPhone model (kept in desktop mode too)
    engine: str = "webkit"
    locale: str = "es-ES"
    timezone: str = "Europe/Madrid"
    start_url: str = START_URL
    theme: str = "dark"  # color scheme reported to web pages
    mode: str = "desktop"
    quality: str = "smooth"  # iPhone + WebKit only: "sharp" renders at x3 resolution
    user_agent: str = ""  # empty = the device's default
    seed: str = ""  # fingerprint of the profile (see stealth.py); kept for its whole life
    notes: str = ""  # free text shown on its card (which account it is, for example)
    color: str = ""  # one of COLORS
    last_opened: str = ""  # ISO date and time it was last opened

    @property
    def folder(self) -> Path:
        return PROFILES_DIR / self.name


# ----------------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------------


def migrate_legacy_data() -> tuple[int, str] | None:
    """Moves the data folder that older builds created next to the .exe into DATA_DIR.

    Must run before anything writes to DATA_DIR (logging included), and returns
    (log level, message) so the caller can log it once logging is set up; None when
    there is nothing to move. It never overwrites data already in DATA_DIR.
    """
    legacy = LEGACY_DATA_DIR
    if legacy is None or not legacy.is_dir():
        return None
    if DATA_DIR.exists() and any(DATA_DIR.iterdir()):
        return logging.WARNING, f"Old data folder left untouched in {legacy}: {DATA_DIR} already has data"
    try:
        DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
        if DATA_DIR.exists():
            DATA_DIR.rmdir()  # empty, so the move can take its place
        # On the same drive this is a rename: instant, whatever the size.
        shutil.move(str(legacy), str(DATA_DIR))
    except OSError as e:
        return logging.ERROR, f"Could not move the data folder from {legacy} to {DATA_DIR}: {e}"
    return logging.INFO, f"Moved the data folder from {legacy} to {DATA_DIR}"


def _read_profiles(path: Path) -> list[Profile]:
    """Raises OSError (unreadable) or ValueError, KeyError, TypeError, AttributeError (damaged)."""
    known = {f.name for f in fields(Profile)}
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = [Profile(**{k: v for k, v in item.items() if k in known}) for item in raw["profiles"]]
    if any(not isinstance(getattr(p, f), str) for p in items for f in known):
        raise TypeError("every profile value must be text")
    return items


def load_profiles() -> dict[str, Profile]:
    """The saved profiles. A damaged file is replaced by its backup (the previous good version)."""
    if not PROFILES_FILE.exists():
        return {}
    try:
        items = _read_profiles(PROFILES_FILE)
    except OSError as e:  # locked or unreadable (permissions, disk)
        log.error("Could not read %s: %s", PROFILES_FILE, e)
        raise ProfileError(f"Could not read {PROFILES_FILE.name}: {e}") from e
    except (ValueError, KeyError, TypeError, AttributeError) as e:  # ValueError: bad JSON or not UTF-8
        log.error("Could not read %s: %s", PROFILES_FILE, e)
        items = _restore_backup(e)
    profiles: dict[str, Profile] = {}
    for profile in items:
        if name_taken(profiles, profile.name):
            log.warning("Ignoring a duplicate of profile '%s' in %s", profile.name, PROFILES_FILE.name)
            continue
        profiles[profile.name] = _sanitize(profile)
    if any(not profile.seed for profile in profiles.values()):
        _give_seeds(profiles)
    log.debug("Loaded %d profile(s)", len(profiles))
    return profiles


def backup_file() -> Path:
    """The previous good version of profiles.json, next to it; used if the file gets damaged."""
    return PROFILES_FILE.with_name(PROFILES_FILE.name + ".bak")


def _restore_backup(error: Exception) -> list[Profile]:
    """Puts the backup back in place of a damaged profiles.json; the damaged one is kept aside."""
    try:
        items = _read_profiles(backup_file())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ProfileError(f"{PROFILES_FILE} is corrupted: {error}") from error
    damaged = PROFILES_FILE.with_name(f"profiles.damaged-{int(PROFILES_FILE.stat().st_mtime)}.json")
    try:
        PROFILES_FILE.replace(damaged)
        shutil.copyfile(backup_file(), PROFILES_FILE)
    except OSError as e:
        raise ProfileError(f"{PROFILES_FILE} is corrupted and its backup could not be restored: {e}") from e
    log.warning("%s was damaged; restored the backup (the damaged file is %s)", PROFILES_FILE.name, damaged.name)
    return items


def _give_seeds(profiles: dict[str, Profile]) -> None:
    """Profiles from older versions get their fingerprint seed, saved so it never changes."""
    missing = [profile for profile in profiles.values() if not profile.seed]
    for profile in missing:
        profile.seed = new_seed()
    try:
        save_profiles(profiles)
        log.info("Gave a fingerprint to %d older profile(s)", len(missing))
    except OSError as e:  # until it can be saved, a seed derived from the name keeps it stable
        for profile in missing:
            profile.seed = ""
        log.warning("Could not save the fingerprints of the profiles: %s", e)


def new_seed() -> str:
    return secrets.token_hex(8)


def _sanitize(profile: Profile) -> Profile:
    """Resets invalid choices (e.g. edited by hand in the file) to their default,
    so a bad value cannot break the app."""
    defaults = {f.name: f.default for f in fields(Profile)}
    choices = (("mode", MODES), ("engine", ENGINES), ("theme", THEMES), ("quality", QUALITIES), ("color", COLORS))
    for field, allowed in choices:
        value = getattr(profile, field)
        if value not in allowed:
            log.warning("Profile '%s': invalid %s '%s', using '%s'", profile.name, field, value, defaults[field])
            setattr(profile, field, defaults[field])
    return profile


def name_taken(profiles: dict[str, Profile], name: str, ignore: str | None = None) -> bool:
    """Whether another profile already uses this name, ignoring case: Windows folders
    are case-insensitive, so "Test" and "test" would share the same session data."""
    return any(existing.lower() == name.lower() and existing != ignore for existing in profiles)


def save_profiles(profiles: dict[str, Profile]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"profiles": [asdict(p) for p in profiles.values()]}, indent=2, ensure_ascii=False)
    # Write to a temporary file and swap it in, so the file is never left half-written.
    temp = PROFILES_FILE.with_suffix(".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(PROFILES_FILE)
    # The same content again as the backup, so a damaged file can be recovered without losing anything.
    backup = backup_file()
    temp = backup.with_name(backup.name + ".tmp")
    with contextlib.suppress(OSError):  # the backup is a safety net; saving does not depend on it
        temp.write_text(content, encoding="utf-8")
        temp.replace(backup)
    log.debug("Saved %d profile(s) to %s", len(profiles), PROFILES_FILE.name)


# ----------------------------------------------------------------------------
# User-agents
# ----------------------------------------------------------------------------


@lru_cache(maxsize=1)
def chromium_major_version() -> int:
    """Major version of the Chromium bundled with Playwright."""
    try:
        path = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
        browsers = json.loads(path.read_text(encoding="utf-8"))["browsers"]
        version = next(b["browserVersion"] for b in browsers if b["name"] == "chromium")
        return int(version.split(".")[0])
    except (OSError, ValueError, KeyError, StopIteration):
        return 140


def iphone_user_agent(device: str) -> str | None:
    """Safari for iOS with a random iOS version the model supports.
    Returns None for models missing from the table (older models)."""
    minimum = IPHONE_MODELS.get(device)
    if minimum is None:
        return None
    major, minor = random.choice([v for v in IOS_VERSIONS if v >= minimum])
    system = "18_6" if major >= 26 else f"{major}_{minor}"
    return (
        f"Mozilla/5.0 (iPhone; CPU iPhone OS {system} like Mac OS X) AppleWebKit/605.1.15 "
        f"(KHTML, like Gecko) Version/{major}.{minor} Mobile/15E148 Safari/604.1"
    )


def desktop_user_agent() -> str:
    """Chrome for Windows with the version of the bundled engine, so the user-agent,
    navigator.userAgentData and the client hints all agree (a different version, or
    Edge, would contradict what the browser itself reports)."""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{chromium_major_version()}.0.0.0 Safari/537.36"
    )


def random_user_agent(mode: str, device: str) -> str | None:
    return desktop_user_agent() if mode == "desktop" else iphone_user_agent(device)


def ios_version(user_agent: str) -> str:
    """'iOS 18.4' from an iPhone user-agent (iOS 26+ is read from 'Version/')."""
    system = re.search(r"OS (\d+)_(\d+)", user_agent)
    safari = re.search(r"Version/(\d+)\.(\d+)", user_agent)
    if system and safari and int(safari.group(1)) >= 26 and system.groups() == ("18", "6"):
        return f"iOS {safari.group(1)}.{safari.group(2)}"
    return f"iOS {system.group(1)}.{system.group(2)}" if system else "iOS"


def browser_label(user_agent: str) -> str:
    """'Chrome 152', 'Edge 151', 'Safari 18.5'… from a desktop user-agent."""
    for pattern, label in (
        (r"Edg/(\d+)", "Edge"),
        (r"Chrome/(\d+)", "Chrome"),
        (r"Version/(\d+\.\d+).*Safari", "Safari"),
    ):
        if match := re.search(pattern, user_agent):
            return f"{label} {match.group(1)}"
    return "Browser"


def pick_device(used: set[str], devices: Devices) -> str:
    """Random model, preferring the ones no profile uses yet."""
    candidates = [d for d in IPHONE_MODELS if d in devices]
    unused = [d for d in candidates if d not in used]
    return random.choice(unused or candidates)


# ----------------------------------------------------------------------------
# Profile operations
# ----------------------------------------------------------------------------


def describe(profile: Profile) -> str:
    """Short summary for the logs: mode, engine, device and user-agent."""
    target = f"{profile.device}, {profile.engine}" if profile.mode == "iphone" else "desktop, chromium"
    return f"{target}, UA: {profile.user_agent or 'device default'}"


def _normalize(profile: Profile) -> Profile:
    """Trims whitespace and applies the fixed rules (desktop mode uses Chromium)."""
    return replace(
        profile,
        engine="chromium" if profile.mode == "desktop" else profile.engine,
        locale=profile.locale.strip(),
        timezone=profile.timezone.strip(),
        start_url=profile.start_url.strip() or START_URL,
        user_agent=profile.user_agent.strip(),
        notes=" ".join(profile.notes.split())[:NOTES_MAX],
    )


def _validate(profile: Profile, devices: Devices) -> None:
    if not NAME_PATTERN.match(profile.name):
        raise ProfileError("The name may only contain letters, digits, '-' and '_' (max. 40).")
    if profile.name.lower() in RESERVED_NAMES:
        raise ProfileError(f"'{profile.name}' is a name reserved by Windows. Choose another one.")
    checks = (
        ("mode", profile.mode, MODES),
        ("engine", profile.engine, ENGINES),
        ("theme", profile.theme, THEMES),
        ("quality", profile.quality, QUALITIES),
        ("color", profile.color, COLORS),
    )
    for label, value, allowed in checks:
        if value not in allowed:
            raise ProfileError(f"Invalid {label}: '{value}'. Use: {', '.join(allowed)}")
    if profile.mode == "iphone" and profile.device not in devices:
        raise ProfileError(f"Unknown device '{profile.device}'.")
    if not URL_PATTERN.match(profile.start_url):
        raise ProfileError("The start URL must begin with http:// or https://")
    if not profile.locale or not profile.timezone:
        raise ProfileError("The language and time zone cannot be empty.")


def _prepare(profile: Profile, profiles: dict[str, Profile], devices: Devices) -> Profile:
    """Normalizes, fills in empty values and validates. Without a device one is
    picked at random; without a user-agent a random one matching the mode and model is generated."""
    profile = _normalize(profile)
    if not profile.seed:
        profile.seed = new_seed()
    if not profile.device:
        profile.device = pick_device({p.device for p in profiles.values()}, devices)
    if not profile.user_agent:
        profile.user_agent = random_user_agent(profile.mode, profile.device) or ""
    _validate(profile, devices)
    return profile


def create_profile(profile: Profile, devices: Devices) -> Profile:
    """Saves a new profile."""
    profiles = load_profiles()
    if name_taken(profiles, profile.name):
        raise ProfileError(f"A profile named '{profile.name}' already exists.")
    profile = _prepare(profile, profiles, devices)
    if profile.folder.exists():
        log.warning("Profile '%s' reuses an existing data folder", profile.name)
    profile.folder.mkdir(parents=True, exist_ok=True)
    profiles[profile.name] = profile
    save_profiles(profiles)
    log.info("Created profile '%s' (%s)", profile.name, describe(profile), extra={"event": "created"})
    return profile


def update_profile(current_name: str, profile: Profile, devices: Devices) -> Profile:
    """Saves the changes to a profile, including its name. It must be closed."""
    profiles = load_profiles()
    if current_name not in profiles:
        raise ProfileError(f"Profile '{current_name}' does not exist.")
    if name_taken(profiles, profile.name, ignore=current_name):
        raise ProfileError(f"A profile named '{profile.name}' already exists.")
    # Editing never changes the fingerprint: the sites keep seeing the same browser.
    current = profiles[current_name]
    profile = replace(
        profile, seed=profile.seed or current.seed, last_opened=profile.last_opened or current.last_opened
    )
    profile = _prepare(profile, profiles, devices)

    source = profiles[current_name].folder
    renamed = profile.name != current_name and source.exists()
    if renamed:
        try:
            source.rename(profile.folder)
        except FileExistsError as e:
            raise ProfileError(f"A data folder named '{profile.name}' already exists.") from e
        except PermissionError as e:
            raise ProfileError(f"Profile '{current_name}' is open. Close it before renaming it.") from e
    # Rebuild the dictionary to keep the list order.
    updated = {
        (profile.name if name == current_name else name): (profile if name == current_name else p)
        for name, p in profiles.items()
    }
    try:
        save_profiles(updated)
    except OSError:
        if renamed:  # keep the folder and the list in agreement
            with contextlib.suppress(OSError):
                profile.folder.rename(source)
        raise
    if profile.name != current_name:
        log.info("Renamed profile '%s' to '%s'", current_name, profile.name, extra={"event": "edited"})
    log.info("Updated profile '%s' (%s)", profile.name, describe(profile), extra={"event": "edited"})
    return profile


@dataclass
class DeletedProfile:
    """A deleted profile, which can still be restored: its data waits in the trash folder."""

    profile: Profile
    index: int  # its place in the list
    folder: Path | None  # its data in the trash folder (None if it had none)


def trash_dir() -> Path:
    """Where the data of deleted profiles waits, while the deletion can still be undone."""
    return PROFILES_FILE.with_name("trash")


def delete_profile(name: str, *, undoable: bool = False) -> DeletedProfile:
    """Deletes a profile and its data for good. With undoable=True the data waits in the trash
    folder until purge() (so restore_profile() can bring it back in the meantime)."""
    profiles = load_profiles()
    if name not in profiles:
        raise ProfileError(f"Profile '{name}' does not exist.")
    profile = profiles[name]
    index = list(profiles).index(name)
    trashed: Path | None = None
    if profile.folder.exists():
        trashed = trash_dir() / f"{name}-{time.time_ns()}"
        try:
            trashed.parent.mkdir(parents=True, exist_ok=True)
            profile.folder.rename(trashed)  # same drive: instant, whatever its size
        except PermissionError as e:
            raise ProfileError(f"Profile '{name}' is open. Close it before deleting it.") from e
        except OSError as e:  # e.g. a file still in use by another program
            raise ProfileError(f"Could not delete the data of '{name}': {e}") from e
    del profiles[name]
    try:
        save_profiles(profiles)
    except OSError as e:
        if trashed is not None:  # keep the folder and the list in agreement
            with contextlib.suppress(OSError):
                trashed.rename(profile.folder)
        raise ProfileError(f"Could not delete '{name}': the list could not be saved: {e}") from e
    log.info("Deleted profile '%s' and its data", name, extra={"event": "deleted"})
    deleted = DeletedProfile(profile, index, trashed)
    if not undoable:
        purge(deleted)
    return deleted


def restore_profile(deleted: DeletedProfile) -> Profile:
    """Undoes delete_profile(undoable=True): the profile goes back to its place, with its data."""
    profile = deleted.profile
    profiles = load_profiles()
    if name_taken(profiles, profile.name):
        raise ProfileError(f"A profile named '{profile.name}' was created in the meantime.")
    if deleted.folder is not None:
        if not deleted.folder.exists():
            raise ProfileError(f"The data of '{profile.name}' is no longer in the trash.")
        try:
            profile.folder.parent.mkdir(parents=True, exist_ok=True)
            deleted.folder.rename(profile.folder)
        except OSError as e:
            raise ProfileError(f"Could not restore the data of '{profile.name}': {e}") from e
    items = list(profiles.items())
    items.insert(min(deleted.index, len(items)), (profile.name, profile))
    try:
        save_profiles(dict(items))
    except OSError as e:
        if deleted.folder is not None:
            with contextlib.suppress(OSError):
                profile.folder.rename(deleted.folder)
        raise ProfileError(f"Could not restore '{profile.name}': {e}") from e
    log.info("Restored profile '%s'", profile.name, extra={"event": "created"})
    return profile


def purge(deleted: DeletedProfile) -> None:
    """Deletes for good the data of a deleted profile (the deletion can no longer be undone)."""
    if deleted.folder is not None:
        _remove(deleted.folder)


def empty_trash() -> None:
    """Deletes what an earlier session left in the trash folder (it closed while a deletion
    could still be undone)."""
    folder = trash_dir()
    if folder.is_dir():
        for item in folder.iterdir():
            _remove(item)


def _remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Could not remove the deleted data in %s: %s", path, e)


def duplicate_profile(name: str, devices: Devices) -> Profile:
    """A new profile with the same settings (mode, engine, language, time zone, start page, theme,
    notes and color), but its own fingerprint and user-agent, a new random iPhone model and no
    session: a separate browser for another account."""
    profiles = load_profiles()
    if name not in profiles:
        raise ProfileError(f"Profile '{name}' does not exist.")
    source = profiles[name]
    copy = replace(
        source,
        name=_copy_name(profiles, name),
        seed="",
        user_agent="",
        device=source.device if source.mode == "desktop" else "",
        last_opened="",
    )
    return create_profile(copy, devices)


def _copy_name(profiles: dict[str, Profile], name: str) -> str:
    """ "account_copy", then "account_copy2"..., within the 40 characters a name may have."""
    number = 1
    while True:
        suffix = "_copy" if number == 1 else f"_copy{number}"
        candidate = name[: 40 - len(suffix)] + suffix
        if not name_taken(profiles, candidate):
            return candidate
        number += 1


def reorder_profiles(names: list[str]) -> None:
    """Saves the profiles in this order (the order of the list in the app)."""
    profiles = load_profiles()
    if sorted(names) != sorted(profiles):
        raise ProfileError("The list of profiles changed; try again.")
    save_profiles({name: profiles[name] for name in names})


def mark_opened(name: str) -> None:
    """Remembers when the profile was last opened (shown on its card)."""
    profiles = load_profiles()
    if name in profiles:
        profiles[name].last_opened = datetime.now().isoformat(timespec="seconds")
        save_profiles(profiles)
