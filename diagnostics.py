"""A summary to paste when reporting a problem: versions, browsers, settings and the latest problems."""

from __future__ import annotations

import logging
import platform
from importlib import metadata

import launcher
import logs
from about import APP_NAME, APP_VERSION
from profiles import BROWSERS_DIR, DATA_DIR, FROZEN, ProfileError, load_profiles
from settings import Settings

PROBLEMS_SHOWN = 30


def report(settings: Settings, handler: logs.PanelHandler) -> str:
    try:
        playwright_version = metadata.version("playwright")
    except metadata.PackageNotFoundError:
        playwright_version = "unknown"
    try:
        profiles = list(load_profiles().values())
        modes = f"{len(profiles)} ({sum(p.mode == 'desktop' for p in profiles)} desktop, "
        modes += f"{sum(p.mode == 'iphone' for p in profiles)} iPhone)"
    except ProfileError as e:
        modes = f"unreadable: {e}"
    folders = launcher.required_browser_folders()
    problems = [entry.line for entry in handler.records(logging.WARNING)][-PROBLEMS_SHOWN:]
    lines = [
        f"{APP_NAME} {APP_VERSION} ({'.exe' if FROZEN else 'source code'})",
        f"System: {platform.platform()}",
        f"Python {platform.python_version()}, Playwright {playwright_version}",
        f"Browsers: {', '.join(folders) or 'unknown'} - {'installed' if launcher.browsers_installed() else 'missing'}",
        f"Browsers folder: {BROWSERS_DIR}",
        f"Data folder: {DATA_DIR}",
        f"Profiles: {modes}",
        f"Settings: mode {settings.mode}, sounds {settings.sounds}, reopen tabs {settings.restore_tabs}",
        "",
        f"Latest warnings and errors ({len(problems)}):" if problems else "No warnings or errors in this session.",
        *problems,
    ]
    return "\n".join(lines)
