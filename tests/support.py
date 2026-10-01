"""A temporary data folder for each test, so the real profiles are never touched."""

from __future__ import annotations

import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import profiles
import settings

logging.getLogger("app").addHandler(logging.NullHandler())  # the expected warnings stay out of the output

# Enough of Playwright's device list for the tests.
DEVICES: dict[str, dict[str, Any]] = {
    "iPhone 15 Pro": {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)",
        "viewport": {"width": 393, "height": 659},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
    "iPhone 13": {
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)",
        "viewport": {"width": 390, "height": 664},
        "device_scale_factor": 3,
        "is_mobile": True,
        "has_touch": True,
    },
}


class DataFolderTest(unittest.TestCase):
    """Points the app's data paths to a temporary folder for the duration of each test."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="openprofiles-test-"))
        self._saved = (profiles.DATA_DIR, profiles.PROFILES_DIR, profiles.PROFILES_FILE, settings.SETTINGS_FILE)
        profiles.DATA_DIR = self.folder
        profiles.PROFILES_DIR = self.folder / "profiles"
        profiles.PROFILES_FILE = self.folder / "profiles.json"
        settings.SETTINGS_FILE = self.folder / "settings.json"

    def tearDown(self) -> None:
        profiles.DATA_DIR, profiles.PROFILES_DIR, profiles.PROFILES_FILE, settings.SETTINGS_FILE = self._saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def create(self, name: str, **values: Any) -> profiles.Profile:
        return profiles.create_profile(profiles.Profile(name=name, device=values.pop("device", ""), **values), DEVICES)
