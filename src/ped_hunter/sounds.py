"""Quiet opt-in feedback cues; no audio overlap and no work on the Tk thread."""
from __future__ import annotations

import io
import math
import struct
import sys
import threading
import time
import wave


def _tone(frequency: float) -> bytes:
    rate = 22050
    count = int(rate * 0.085)
    frames = bytearray()
    for i in range(count):
        envelope = math.sin(math.pi * i / count) ** 2
        sample = int(1100 * envelope * math.sin(2 * math.pi * frequency * i / rate))
        frames.extend(struct.pack("<h", sample))
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(frames)
    return output.getvalue()


class CuePlayer:
    def __init__(self) -> None:
        self._last_played = float("-inf")
        self._busy = threading.Lock()
        self._tones: dict[str, bytes] = {}

    def play(self, kind: str) -> None:
        now = time.monotonic()
        if sys.platform != "win32" or now - self._last_played < 0.8 or not self._busy.acquire(blocking=False):
            return
        self._last_played = now

        def worker() -> None:
            try:
                import winsound
                if kind not in self._tones:
                    self._tones[kind] = _tone(740 if kind == "loot" else 980)
                winsound.PlaySound(self._tones[kind], winsound.SND_MEMORY | winsound.SND_SYNC | winsound.SND_NODEFAULT)
            except (ImportError, OSError, RuntimeError):
                pass  # Muted/unavailable audio must never affect tracking.
            finally:
                self._busy.release()

        threading.Thread(target=worker, daemon=True, name="ped-feedback-cue").start()
