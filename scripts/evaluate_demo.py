"""Mix a supplied waveform with seeded noise for an engineering smoke check."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import wave

import numpy as np


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as file:
        if (file.getnchannels(), file.getsampwidth(), file.getframerate()) != (1, 2, 48000):
            raise ValueError("expected mono PCM16 48 kHz")
        return np.frombuffer(file.readframes(file.getnframes()), dtype="<i2").astype(np.float64) / 32768


def write_wav(path: Path, audio: np.ndarray) -> None:
    with path.open("xb") as dest, wave.open(dest, "wb") as file:
        file.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        pcm = np.rint(np.clip(audio * 32768, -32768, 32767)).astype("<i2")
        file.writeframes(pcm.tobytes())


def snr(clean: np.ndarray, measured: np.ndarray) -> float:
    return float(10 * np.log10(np.sum(clean**2) / max(np.sum((clean-measured)**2), 1e-20)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("output/demo"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = read_wav(args.source)
    # Include two seconds of noise-only context, one second after speech.
    source *= 0.6 / max(np.max(np.abs(source)), 1e-10)
    clean = np.pad(source, (96000, 48000))
    rng = np.random.default_rng(20261013)
    t = np.arange(len(clean)) / 48000
    noise = rng.standard_normal(len(clean)) * 0.015
    noise += 0.018 * np.sin(2*np.pi*120*t) + 0.01 * np.sin(2*np.pi*240*t)
    # Repeatable short broadband taps, a crude keyboard-like interference.
    for start in range(72000, len(clean)-600, 31000):
        noise[start:start+600] += rng.standard_normal(600) * 0.08 * np.exp(-np.arange(600)/90)
    for name, samples in (("clean.wav", clean), ("noisy.wav", clean+noise)):
        write_wav(args.output/name, samples)
    run = subprocess.run([str(args.executable.resolve()), "wav", str(args.output/"noisy.wav"),
                          str(args.output/"denoised.wav"), "--atten-lim-db", "24"],
                         capture_output=True, text=True, check=True, timeout=120)
    out = read_wav(args.output/"denoised.wav")
    noisy = read_wav(args.output/"noisy.wav")
    reference = read_wav(args.output/"clean.wav")
    assert len(out) == len(noisy) == len(reference)
    region = slice(96000, 96000+len(source))
    # Exclude startup and pre-speech boundary from noise-only level measurement.
    quiet = slice(48000, 90000)
    report = {
        "fixture": "user-supplied source waveform plus seeded noise, hum and taps; source provenance not asserted",
        "scope": "one artificial fixture; not RNNoise comparison, human rating, WER or production validation",
        "samples": len(out),
        "input_speech_region_snr_db": snr(reference[region], noisy[region]),
        "output_speech_region_snr_db": snr(reference[region], out[region]),
        "noise_only_reduction_db": float(10*np.log10(np.mean(noisy[quiet]**2)/max(np.mean(out[quiet]**2), 1e-20))),
        "timing": json.loads(run.stderr.strip().splitlines()[-1]),
        "sha256": {name: hashlib.sha256((args.output/name).read_bytes()).hexdigest()
                   for name in ("clean.wav", "noisy.wav", "denoised.wav")},
    }
    (args.output/"report.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
