"""App settings (appearance mode and notification sounds), saved in settings.json in the data folder."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import cast

from profiles import DATA_DIR

log = logging.getLogger("app.settings")

SETTINGS_FILE = DATA_DIR / "settings.json"
APPEARANCE_MODES = ("auto", "light", "dark")  # auto follows the Windows theme


@dataclass
class Settings:
    mode: str = "auto"  # follows Windows: dark when Windows uses dark mode
    sounds: bool = True  # notification sound


def load_settings() -> Settings:
    """Reads the settings; a missing, unreadable or invalid file gives the defaults."""
    settings = Settings()
    try:
        raw: object = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return settings
    except (OSError, ValueError) as e:
        log.warning("Could not read %s, using the default settings: %s", SETTINGS_FILE.name, e)
        return settings
    values = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    mode = values.get("mode")
    if isinstance(mode, str) and mode in APPEARANCE_MODES:
        settings.mode = mode
    elif mode is not None:
        log.warning("Ignoring the unknown appearance mode %r in %s", mode, SETTINGS_FILE.name)
    sounds = values.get("sounds")
    if isinstance(sounds, bool):
        settings.sounds = sounds
    elif sounds is not None:
        log.warning("Ignoring the invalid value %r of sounds in %s", sounds, SETTINGS_FILE.name)
    return settings


def save_settings(settings: Settings) -> None:
    """Raises OSError if the file cannot be written."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Write to a temporary file and swap it in, so the file is never left half-written.
    temp = SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    temp.replace(SETTINGS_FILE)
    log.debug("Saved the settings to %s", SETTINGS_FILE.name)
