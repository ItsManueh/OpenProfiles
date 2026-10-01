"""
Notification sound: a short three-note chime, synthesized here (no audio files in
the repository). It reproduces the "success" sound of SpotiFLAC
(github.com/spotbye/SpotiFLAC, MIT license), which plays three sine tones,
C5, E5 and G5, each fading out exponentially.

It only plays for the events that change the list of profiles (created, edited,
deleted) and when the browsers finish downloading. The first time it is written as a small WAV file to the temporary
folder, and Windows plays it asynchronously (the app never waits).
"""

from __future__ import annotations

import array
import logging
import math
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("app.gui")

SAMPLE_RATE = 44_100
SOUND_EVENTS = ("created", "edited", "deleted", "downloaded")  # the only notifications with sound
MIN_INTERVAL_SECONDS = 0.5  # events arriving together sound only once
SOUNDS_DIR = Path(tempfile.gettempdir()) / "OpenProfiles" / "sounds"


@dataclass(frozen=True)
class Tone:
    frequency: float  # Hz
    start: float  # seconds from the beginning of the sound
    duration: float  # seconds until the tone has faded to 0.01
    volume: float  # starting gain, 0..1


# SpotiFLAC's playSuccess(): C5 for 80 ms, E5 for 80 ms, then G5 for 150 ms (a little louder).
CHIME = (
    Tone(523.25, 0.00, 0.08, 0.20),
    Tone(659.25, 0.08, 0.08, 0.20),
    Tone(783.99, 0.16, 0.15, 0.25),
)
ATTACK_SECONDS = 0.001  # the gain rises this fast instead of jumping, which would click
RELEASE_SECONDS = 0.006  # and falls to silence after the fade, instead of stopping abruptly
FLOOR = 0.01  # the fade ends at this gain, as in the Web Audio original
VERSION = 4  # bump when the sound changes, so the cached file is rewritten


def synthesize(tones: tuple[Tone, ...]) -> bytes:
    """16-bit mono PCM of the tones."""
    length = max(tone.start + tone.duration + RELEASE_SECONDS for tone in tones)
    samples = array.array("h", [0]) * (int(length * SAMPLE_RATE) + 1)
    for tone in tones:
        first = int(tone.start * SAMPLE_RATE)
        step = 2 * math.pi * tone.frequency / SAMPLE_RATE
        # exponentialRampToValueAtTime: gain = volume * (floor / volume) ** (t / duration)
        ratio = FLOOR / tone.volume
        count = int((tone.duration + RELEASE_SECONDS) * SAMPLE_RATE)
        for i in range(min(count, len(samples) - first)):
            t = i / SAMPLE_RATE
            if t <= tone.duration:
                gain = tone.volume * ratio ** (t / tone.duration)
            else:
                gain = FLOOR * (1 - (t - tone.duration) / RELEASE_SECONDS)
            gain *= min(t / ATTACK_SECONDS, 1.0)
            mixed = samples[first + i] + gain * math.sin(step * i) * 32767
            samples[first + i] = max(-32767, min(32767, int(mixed)))
    return samples.tobytes()


_files_lock = threading.Lock()  # the startup thread and a first notification may ask at once


def sound_file() -> Path:
    """WAV file of the chime, created the first time."""
    path = SOUNDS_DIR / f"chime-v{VERSION}.wav"
    with _files_lock:
        if not path.exists():
            SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".tmp")
            with wave.open(str(temp), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(SAMPLE_RATE)
                out.writeframes(synthesize(CHIME))
            temp.replace(path)  # never leave a half-written file behind
    return path


def _prepare_file() -> None:
    try:
        sound_file()
    except OSError as e:
        log.debug("Could not prepare the notification sound: %s", e)


class NotificationSound:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._last = float("-inf")
        # Written in the background at startup, so the first notification does not wait for it.
        threading.Thread(target=_prepare_file, name="sound", daemon=True).start()

    def play_for(self, kind: str) -> None:
        """Plays the chime if this kind of notification has sound (see SOUND_EVENTS)."""
        if kind in SOUND_EVENTS:
            self.play()

    def play(self) -> None:
        now = time.monotonic()
        if not self.enabled or now - self._last < MIN_INTERVAL_SECONDS:
            return
        self._last = now
        if sys.platform != "win32":
            return
        import winsound

        try:
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            winsound.PlaySound(str(sound_file()), flags)
        except (OSError, RuntimeError) as e:
            log.debug("Could not play the notification sound: %s", e)
