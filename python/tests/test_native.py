"""Opt-in model/CLI/Pipecat end-to-end regression, not a speech-quality test.

Set SAO_DENOISE_EXE to a built sao-denoise executable and install the Pipecat
extra. With no executable configured, the normal unit-test run skips this test.
"""

from __future__ import annotations

import asyncio
import importlib.util
import itertools
import math
import os
from pathlib import Path
import random
import struct
import tempfile
import unittest
import wave


NATIVE_EXE = os.environ.get("SAO_DENOISE_EXE")
HAS_PIPECAT = importlib.util.find_spec("pipecat") is not None


def synthetic_pcm():
    """Twelve hops including a real silent gap, then four trailing zero hops."""
    noise = random.Random(821)
    samples = []
    for index in range(12 * 480):
        if 2 <= index // 480 <= 4:
            sample = 0
        else:
            phase = 2 * math.pi * index / 48_000
            sample = round(7000 * math.sin(137 * phase)
                           + 2500 * math.sin(274 * phase)
                           + 500 * noise.uniform(-1, 1))
        samples.append(sample)
    source = struct.pack(f"<{len(samples)}h", *samples)
    return source, source + bytes(4 * 960)


async def feed_irregular(audio_filter, audio, sizes=(2, 394, 1726, 14, 4000, 962)):
    output = bytearray()
    offset = 0
    for size in itertools.cycle(sizes):
        if offset >= len(audio):
            break
        chunk = audio[offset:offset + size]
        output.extend(await audio_filter.filter(chunk))
        offset += len(chunk)
    return bytes(output)


@unittest.skipUnless(NATIVE_EXE, "set SAO_DENOISE_EXE for actual native integration")
@unittest.skipUnless(HAS_PIPECAT, "install sao-noise[pipecat] for native integration")
class NativePipecatTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_framing_reset_toggles_and_wav_agree(self):
        from pipecat.frames.frames import FilterUpdateSettingsFrame
        from sao_noise.pipecat import DeepFilterNetFilter

        executable = str(Path(NATIVE_EXE).resolve())
        self.assertTrue(Path(executable).is_file(), executable)
        audio_filter = DeepFilterNetFilter(executable, atten_lim_db=24)
        self.addAsyncCleanup(audio_filter.stop)
        source, audio = synthetic_pcm()

        await audio_filter.start(48000)
        info = audio_filter.denoiser.stream_info
        self.assertEqual((info.sample_rate, info.frame_samples, info.model),
                         (48000, 480, "DeepFilterNet3"))
        self.assertEqual(info.algorithmic_delay_samples, 1440)
        delay_bytes = info.algorithmic_delay_samples * 2
        baseline = await feed_irregular(audio_filter, audio)
        self.assertEqual(len(baseline), len(audio))
        self.assertTrue(any(baseline), "native model returned only zeros")
        self.assertTrue(any(baseline[len(source):len(source) + delay_bytes]),
                        "trailing silence failed to drain the model's signal tail")

        # A fresh native state and different call boundaries must produce the
        # same samples. This also checks that stop/start clears all client state.
        await audio_filter.stop()
        await audio_filter.start(48000)
        repeated = await feed_irregular(audio_filter, audio, (958, 2, 1922, 12, 600))
        self.assertEqual(repeated, baseline)

        # Compare real wet/dry toggles with independently constructed expected
        # sample positions. Toggles deliberately bisect native frame boundaries.
        await audio_filter.stop()
        await audio_filter.start(48000)
        segments = [(True, 197 * 2), (False, 803 * 2), (True, 440 * 2),
                    (False, 960 * 2), (True, len(audio) - 2400 * 2)]
        selected = bytearray()
        modes = bytearray([1]) * delay_bytes
        offset = 0
        for enabled, length in segments:
            await audio_filter.process_frame(FilterUpdateSettingsFrame(settings={"enabled": enabled}))
            segment = audio[offset:offset + length]
            selected.extend(await feed_irregular(audio_filter, segment))
            modes.extend(bytes([int(enabled)]) * len(segment))
            offset += length
        delayed_dry = bytes(delay_bytes) + audio
        expected = bytes(wet if enabled else dry
                         for wet, dry, enabled in zip(baseline, delayed_dry, modes))
        self.assertEqual(selected, expected)
        await audio_filter.stop()

        # WAV mode compensates latency. Its samples should match the streaming
        # path after removing exactly the reported signal-alignment delay.
        with tempfile.TemporaryDirectory(prefix="sao-native-test-") as directory:
            input_path = Path(directory) / "input.wav"
            output_path = Path(directory) / "output.wav"
            with wave.open(str(input_path), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(48000)
                writer.writeframes(source)
            process = await asyncio.create_subprocess_exec(
                executable, "wav", str(input_path), str(output_path),
                "--atten-lim-db", "24", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()
            self.assertEqual(process.returncode, 0, stderr.decode(errors="replace"))
            self.assertEqual(stdout, b"")
            with wave.open(str(output_path), "rb") as reader:
                self.assertEqual((reader.getnchannels(), reader.getsampwidth(), reader.getframerate()),
                                 (1, 2, 48000))
                self.assertEqual(reader.getnframes(), len(source) // 2)
                wav_audio = reader.readframes(reader.getnframes())
            self.assertEqual(wav_audio, baseline[delay_bytes:delay_bytes + len(source)])


if __name__ == "__main__":
    unittest.main()
