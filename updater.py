"""
Updates the .exe to the latest release published on GitHub.

1. download_update() downloads OpenProfiles.exe of the latest release next to the
   running one and checks it against the SHA-256 published with it.
2. install_update() starts a small hidden script that waits for the app to close,
   puts the new .exe in place of the old one and opens it again.

Only the packaged .exe updates itself; from the source code, git does.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import cast

from about import APP_NAME, APP_VERSION, LATEST_RELEASE_API
from profiles import FROZEN

log = logging.getLogger("app.gui")

EXE_NAME = "OpenProfiles.exe"
TIMEOUT_SECONDS = 30
CHUNK = 1 << 16
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{APP_VERSION}"}


class UpdateError(Exception):
    """The update could not be downloaded or checked; the message is meant for the user."""


def can_update() -> bool:
    return FROZEN and sys.platform == "win32"


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def release_assets(api_url: str = LATEST_RELEASE_API) -> tuple[str, str, str]:
    """(tag, url of the .exe, url of its checksum) of the latest release."""
    try:
        raw: object = json.loads(_get(api_url).decode("utf-8"))
    except (OSError, ValueError) as e:
        raise UpdateError(f"Could not reach GitHub: {e}") from e
    release = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    tag = release.get("tag_name")
    assets = release.get("assets")
    urls: dict[str, str] = {}
    for asset in cast("list[object]", assets) if isinstance(assets, list) else []:
        item = cast("dict[str, object]", asset) if isinstance(asset, dict) else {}
        name, url = item.get("name"), item.get("browser_download_url")
        if isinstance(name, str) and isinstance(url, str):
            urls[name] = url
    if not isinstance(tag, str) or EXE_NAME not in urls or f"{EXE_NAME}.sha256" not in urls:
        raise UpdateError("The latest release has no .exe to update to yet.")
    return tag, urls[EXE_NAME], urls[f"{EXE_NAME}.sha256"]


def download_update(
    progress: Callable[[int, int], None] | None = None, api_url: str = LATEST_RELEASE_API, folder: Path | None = None
) -> Path:
    """Downloads the new .exe next to the current one (or into `folder`) and checks it.
    `progress(done, total)` follows the download. Raises UpdateError."""
    tag, exe_url, checksum_url = release_assets(api_url)
    try:
        expected = re.search(r"\b[0-9a-fA-F]{64}\b", _get(checksum_url).decode("utf-8", "replace"))
    except OSError as e:
        raise UpdateError(f"Could not download the checksum: {e}") from e
    if expected is None:
        raise UpdateError("The checksum of the release is not valid.")
    target_folder = folder or current_exe().parent
    target = target_folder / f"OpenProfiles-{tag}.download"
    digest = hashlib.sha256()
    try:
        request = urllib.request.Request(exe_url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response, target.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while chunk := response.read(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except OSError as e:
        target.unlink(missing_ok=True)
        if isinstance(e, PermissionError):
            raise UpdateError(f"Cannot write next to the app ({target_folder}). Download it from GitHub.") from e
        raise UpdateError(f"Could not download the update: {e}") from e
    if digest.hexdigest().lower() != expected.group(0).lower():
        target.unlink(missing_ok=True)
        raise UpdateError("The downloaded file does not match its checksum; it was discarded.")
    log.info("Downloaded and checked the update %s (%s)", tag, target)
    return target


# Paths travel in environment variables, so any character in them is safe. The Windows tools are
# called by their full path: another "find" (Git's, for example) may come first in PATH.
SCRIPT = r"""@echo off
rem Waits for OpenProfiles to close, puts the new version in place and opens it again.
set "tools=%SystemRoot%\System32"
:wait
"%tools%\tasklist.exe" /FI "PID eq %OP_PID%" /NH 2>nul | "%tools%\find.exe" "%OP_PID%" >nul && (call :pause & goto wait)
set tries=0
:move
move /y "%OP_NEW%" "%OP_CURRENT%" >nul 2>&1 && goto start
set /a tries+=1
if %tries% lss 20 (call :pause & goto move)
:start
start "" "%OP_CURRENT%"
del "%~f0" & exit /b
:pause
"%tools%\ping.exe" -n 2 127.0.0.1 >nul
exit /b
"""


def install_update(new_exe: Path, target: Path | None = None, wait_for: int | None = None) -> None:
    """Starts the hidden script that replaces `target` (the running .exe) with `new_exe` once
    the process `wait_for` has ended, and opens it. The caller then closes the app.

    In a packaged .exe the code runs in a child of the process that holds the file; that
    parent ends last, so it is the one to wait for."""
    target = target or current_exe()
    pid = wait_for if wait_for is not None else os.getppid()
    script = Path(tempfile.gettempdir()) / f"openprofiles-update-{os.getpid()}.cmd"
    script.write_text(SCRIPT, encoding="ascii")
    environment = {**os.environ, "OP_PID": str(pid), "OP_NEW": str(new_exe), "OP_CURRENT": str(target)}
    subprocess.Popen(
        ["cmd.exe", "/d", "/c", str(script)],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    log.info("The update will be installed when the app closes")
