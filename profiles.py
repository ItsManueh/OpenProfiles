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
import shutil
import sys
from dataclasses import asdict, dataclass, fields, replace
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


def load_profiles() -> dict[str, Profile]:
    if not PROFILES_FILE.exists():
        return {}
    known = {f.name for f in fields(Profile)}
    try:
        raw = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        items = [Profile(**{k: v for k, v in item.items() if k in known}) for item in raw["profiles"]]
        if any(not isinstance(getattr(p, f), str) for p in items for f in known):
            raise TypeError("every profile value must be text")
    except OSError as e:  # locked or unreadable (permissions, disk)
        log.error("Could not read %s: %s", PROFILES_FILE, e)
        raise ProfileError(f"Could not read {PROFILES_FILE.name}: {e}") from e
    except (ValueError, KeyError, TypeError, AttributeError) as e:  # ValueError: bad JSON or not UTF-8
        log.error("Could not read %s: %s", PROFILES_FILE, e)
        raise ProfileError(f"{PROFILES_FILE} is corrupted: {e}") from e
    profiles: dict[str, Profile] = {}
    for profile in items:
        if name_taken(profiles, profile.name):
            log.warning("Ignoring a duplicate of profile '%s' in %s", profile.name, PROFILES_FILE.name)
            continue
        profiles[profile.name] = _sanitize(profile)
    log.debug("Loaded %d profile(s)", len(profiles))
    return profiles


def _sanitize(profile: Profile) -> Profile:
    """Resets invalid choices (e.g. edited by hand in the file) to their default,
    so a bad value cannot break the app."""
    defaults = {f.name: f.default for f in fields(Profile)}
    for field, allowed in (("mode", MODES), ("engine", ENGINES), ("theme", THEMES), ("quality", QUALITIES)):
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
    """Chrome (or Edge, 30 %) for Windows with a version close to the bundled one."""
    latest = chromium_major_version()
    version = random.randint(latest - 2, latest)
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36"
    )
    return user_agent + f" Edg/{version}.0.0.0" if random.random() < 0.3 else user_agent


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


def delete_profile(name: str) -> None:
    profiles = load_profiles()
    if name not in profiles:
        raise ProfileError(f"Profile '{name}' does not exist.")
    if profiles[name].folder.exists():
        try:
            shutil.rmtree(profiles[name].folder)
        except PermissionError as e:
            raise ProfileError(f"Profile '{name}' is open. Close it before deleting it.") from e
        except OSError as e:  # e.g. a file still in use by another program
            raise ProfileError(f"Could not delete the data of '{name}': {e}") from e
    del profiles[name]
    save_profiles(profiles)
    log.info("Deleted profile '%s' and its data", name, extra={"event": "deleted"})
