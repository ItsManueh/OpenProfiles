"""App identity: name, version and where its releases are published."""

from __future__ import annotations

APP_NAME = "OpenProfiles"
APP_VERSION = "0.3.0"  # keep in sync with the tag of the GitHub release (v0.3.0)
REPOSITORY = "ItsManueh/OpenProfiles"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


def version_key(tag: str) -> tuple[int, ...]:
    """(0, 2, 0) for "v0.2.0"; anything that is not a number counts as 0."""
    parts = tag.strip().lstrip("vV").split("-")[0].split(".")
    return tuple(int(part) if part.isdigit() else 0 for part in parts)


def is_newer(tag: str, current: str = APP_VERSION) -> bool:
    return version_key(tag) > version_key(current)
