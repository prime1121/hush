"""Generate deterministic voiced/noise WAVs without speech or human recordings."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import struct
import wave

RATE = 48_000
SECONDS = 8


def fixture_pcm() -> tuple[bytes, bytes]:
    rng = random.Random(20261025)
    clean = bytearray()
    noisy = bytearray()
    phase = 0.0
    for index in range(RATE * SECONDS):
        time = index / RATE
        voice = 0.0
        if 2.0 <= time < 7.0:
            local = time - 2.0
            syllable = local % 0.48
            envelope = math.sin(math.pi * min(syllable / 0.34, 1.0)) ** 2 if syllable < 0.34 else 0.0
            frequency = 128 + 22 * math.sin(2 * math.pi * 0.7 * local)
            phase += 2 * math.pi * frequency / RATE
            voice = envelope * sum(math.sin(harmonic * phase) / harmonic ** 1.35
                                   for harmonic in range(1, 9)) * 0.24
        interference = rng.uniform(-1, 1) * 0.035 + 0.018 * math.sin(2 * math.pi * 120 * time)
        tap = index % 31_000
        if tap < 600:
            interference += rng.uniform(-1, 1) * 0.1 * math.exp(-tap / 90)
        for output, value in ((clean, voice), (noisy, voice + interference)):
            output.extend(struct.pack("<h", max(-32768, min(32767, round(value * 32768)))))
    return bytes(clean), bytes(noisy)


def generate_fixture(directory: Path) -> dict:
    """Create a new directory; never overwrite prior evidence or user audio."""
    directory.mkdir(parents=True, exist_ok=False)
    clean, noisy = fixture_pcm()
    hashes = {}
    for name, pcm in (("clean.wav", clean), ("noisy.wav", noisy)):
        path = directory / name
        with path.open("xb") as destination, wave.open(destination, "wb") as output:
            output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            output.writeframes(pcm)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"fixture": "artificial harmonic voiced waveform plus seeded noise, hum and taps",
              "contains_intelligible_speech": False, "contains_human_recording": False,
              "scope": "engineering smoke checks only; not a speech quality benchmark",
              "sample_rate": RATE, "channels": 1, "duration_seconds": SECONDS,
              "speech_start_seconds": 2, "speech_end_seconds": 7, "sha256": hashes}
    (directory / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output/demo"))
    args = parser.parse_args()
    print(json.dumps(generate_fixture(args.output), indent=2))


if __name__ == "__main__":
    main()
