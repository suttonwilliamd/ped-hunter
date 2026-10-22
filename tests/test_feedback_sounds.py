import io
import struct
import wave

from ped_hunter.sounds import _tone
from ped_hunter.cli import build_parser


def test_feedback_tone_is_short_low_amplitude_pcm():
    with wave.open(io.BytesIO(_tone(740)), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getnframes() / audio.getframerate() < 0.1
        data = audio.readframes(audio.getnframes())
    samples = struct.unpack(f"<{len(data) // 2}h", data)
    assert max(abs(sample) for sample in samples) <= 1100


def test_demo_is_explicit_and_not_the_default():
    parser = build_parser()
    assert not parser.parse_args(["gui"]).preview
    assert parser.parse_args(["gui", "--preview"]).preview
