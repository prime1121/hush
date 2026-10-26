"""Measure actual Pipecat-filter/native-process call latency, excluding startup."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import random
import struct
import time

from sao_noise.pipecat import DeepFilterNetFilter


async def run(executable: Path, frames: int) -> dict:
    client = DeepFilterNetFilter(executable.resolve())
    rng = random.Random(714)
    audio = [struct.pack("<480h", *[
        round(6500 * math.sin(2*math.pi*137*(frame*480+i)/48000)
              + 1000*rng.uniform(-1, 1)) for i in range(480)]) for frame in range(frames)]
    timings = []
    setup = time.perf_counter()
    await client.start(48000)
    startup_seconds = time.perf_counter()-setup
    try:
        for frame in audio:
            start = time.perf_counter()
            output = await client.filter(frame)
            timings.append(time.perf_counter()-start)
            assert len(output) == 960
    finally:
        await client.stop()
    ordered = sorted(timings)
    return {
        "fixture": "deterministic synthetic sine plus noise; throughput only",
        "timing_scope": "await Pipecat filter call incl Rust inference, PCM conversion, buffering and IPC; excludes startup, capture, network, scheduling at real-time pace",
        "frames": frames,
        "audio_seconds": frames*0.01,
        "startup_seconds": startup_seconds,
        "filter_compute_seconds": sum(timings),
        "real_time_factor": sum(timings)/(frames*0.01),
        "frame_p50_ms": ordered[round((frames-1)*0.5)]*1000,
        "frame_p95_ms": ordered[round((frames-1)*0.95)]*1000,
        "frame_max_ms": max(timings)*1000,
        "frames_over_10_ms": sum(value > 0.01 for value in timings),
        "warmup_frames": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.frames <= 100_000:
        parser.error("frames must be 1..100000")
    print(json.dumps(asyncio.run(run(args.executable, args.frames)), indent=2))
