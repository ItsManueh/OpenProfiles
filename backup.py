"""
Backups of profiles: exports them (settings, fingerprint and session data) to a .zip
file and imports them back.

Caches are left out: they are big and the browsers rebuild them. The sessions are
encrypted with the Windows account (the browsers' cookies and the app's session
file), so they only work again on the same computer and Windows user; elsewhere the
profiles keep their settings and fingerprint, and you sign in again.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import zipfile
from dataclasses import asdict, fields, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import cast

import profiles as storage
from about import APP_VERSION
from profiles import NAME_PATTERN, RESERVED_NAMES, Profile, ProfileError, load_profiles, name_taken, save_profiles

log = logging.getLogger("app.profiles")

MANIFEST = "openprofiles-backup.json"
# Folders the browsers rebuild by themselves, and their lock files.
SKIPPED_FOLDERS = {
    "crashpad",
    "safe browsing",
    "component_crx_cache",
    "optimization_guide_model_store",
    "browsermetrics",
}
SKIPPED_FILES = {"lockfile", "singletonlock", "singletoncookie", "singletonsocket"}


def _skipped(relative: Path) -> bool:
    folders = [part.lower() for part in relative.parts[:-1]]
    if any(folder.endswith("cache") or folder in SKIPPED_FOLDERS for folder in folders):
        return True
    return relative.name.lower() in SKIPPED_FILES


def export_profiles(path: Path, names: list[str] | None = None) -> tuple[int, int]:
    """Writes the profiles (all, or `names`) to the .zip file `path`; they must be closed.
    Returns (profiles, bytes written). Raises ProfileError or OSError."""
    profiles = load_profiles()
    selected = [profiles[name] for name in (names or list(profiles)) if name in profiles]
    if not selected:
        raise ProfileError("There are no profiles to export.")
    manifest = {
        "app": APP_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "profiles": [asdict(profile) for profile in selected],
    }
    temp = path.with_name(path.name + ".part")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False))
            for profile in selected:
                if not profile.folder.is_dir():
                    continue
                for file in profile.folder.rglob("*"):
                    relative = file.relative_to(profile.folder)
                    if file.is_file() and not _skipped(relative):
                        archive.write(file, f"profiles/{profile.name}/{relative.as_posix()}")
        temp.replace(path)
    except (OSError, zipfile.BadZipFile) as e:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise OSError(f"Could not write the backup: {e}") from e
    size = path.stat().st_size
    log.info("Exported %d profile(s) to %s (%d bytes)", len(selected), path, size, extra={"event": "saved"})
    return len(selected), size


def import_profiles(path: Path) -> list[str]:
    """Adds the profiles of a backup; a name that already exists gets "_imported". Returns the
    names given to them. Raises ProfileError (not a valid backup) or OSError."""
    created: list[Path] = []
    try:
        with zipfile.ZipFile(path) as archive:
            incoming = _read_manifest(archive)
            profiles = load_profiles()
            imported: list[str] = []
            for profile in incoming:
                name = _free_name(profiles, profile.name)
                target = storage.PROFILES_DIR / name
                created.append(target)
                _extract(archive, profile.name, target)
                profiles[name] = replace(profile, name=name)
                imported.append(name)
        save_profiles(profiles)
    except BaseException as error:
        for folder in created:  # nothing half-imported is left behind
            shutil.rmtree(folder, ignore_errors=True)
        if isinstance(error, (zipfile.BadZipFile, ValueError)) and not isinstance(error, ProfileError):
            raise ProfileError(f"The backup could not be read: {error}") from error
        raise
    load_profiles()  # checks and fixes the values that came in, like those of any other profile
    log.info("Imported %d profile(s) from %s", len(imported), path, extra={"event": "created"})
    return imported


def _read_manifest(archive: zipfile.ZipFile) -> list[Profile]:
    known = {f.name for f in fields(Profile)}
    try:
        raw: object = json.loads(archive.read(MANIFEST).decode("utf-8"))
    except KeyError as e:
        raise ProfileError("This file is not an OpenProfiles backup.") from e
    manifest = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    items = manifest.get("profiles")
    if not isinstance(items, list):
        raise ProfileError("This file is not an OpenProfiles backup.")
    incoming: list[Profile] = []
    for item in cast("list[object]", items):
        values = cast("dict[str, object]", item) if isinstance(item, dict) else {}
        fields_ = {key: value for key, value in values.items() if key in known}
        name = fields_.get("name")
        # The name becomes a folder: only the names the app itself allows.
        if not isinstance(name, str) or not NAME_PATTERN.match(name) or name.lower() in RESERVED_NAMES:
            raise ProfileError("The backup is damaged.")
        if not all(isinstance(value, str) for value in fields_.values()):
            raise ProfileError("The backup is damaged.")
        incoming.append(Profile(**cast("dict[str, str]", fields_)))  # pyright: ignore[reportArgumentType]
    if not incoming:
        raise ProfileError("The backup has no profiles.")
    return incoming


def _free_name(profiles: dict[str, Profile], name: str) -> str:
    number = 1
    candidate = name
    while name_taken(profiles, candidate) or (storage.PROFILES_DIR / candidate).exists():
        suffix = "_imported" if number == 1 else f"_imported{number}"
        candidate = name[: 40 - len(suffix)] + suffix
        number += 1
    return candidate


def _extract(archive: zipfile.ZipFile, name: str, target: Path) -> None:
    """Extracts the files of one profile into `target`, refusing any path that would leave it."""
    prefix = f"profiles/{name}/"
    target.mkdir(parents=True, exist_ok=False)
    root = target.resolve()
    for member in archive.infolist():
        if member.is_dir() or not member.filename.startswith(prefix):
            continue
        relative = PurePosixPath(member.filename[len(prefix) :])
        destination = (root / Path(*relative.parts)).resolve()
        if ".." in relative.parts or relative.is_absolute() or not destination.is_relative_to(root):
            raise ProfileError("The backup contains an unsafe path.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, destination.open("wb") as out:
            while chunk := source.read(1 << 20):
                out.write(chunk)
