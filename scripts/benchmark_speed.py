"""Repeated, paired SAO wrapper/native speed measurements with PCM parity checks.

Each measurement uses a fresh Python interpreter and native worker. Input is
mono PCM16 WAV at 48 kHz. This measures unpaced processing, not microphone,
network, playback or end-to-end conversational latency.
"""
from __future__ import annotations

import argparse
import asyncio
from array import array
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import wave

RATE = 48_000
HOP = 480
PACKETS = (480, 1536)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(),
                source.getcomptype()) != (1, 2, RATE, "NONE"):
            raise ValueError("Expected mono PCM16 WAV at 48000 Hz")
        pcm = source.readframes(source.getnframes())
        if not pcm or len(pcm) != source.getnframes() * 2:
            raise ValueError("Expected complete, nonempty PCM audio")
        return pcm


def package_root(path: Path) -> Path:
    path = path.resolve(strict=True)
    if path.name == "sao_noise" and (path / "stream.py").is_file():
        return path.parent
    for relative in ("sao_noise/stream.py", "src/sao_noise/stream.py",
                     "python/src/sao_noise/stream.py"):
        stream = path / relative
        if stream.is_file():
            return stream.parent.parent
    raise ValueError(f"Cannot find sao_noise/stream.py beneath {path}")


def distribution(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "total_seconds": 0, "p50_ms": None,
                "p95_ms": None, "p99_ms": None, "max_ms": None}
    ordered = sorted(values)
    return {"count": len(values), "total_seconds": sum(values),
            **{f"p{int(p * 100)}_ms": ordered[round((len(ordered) - 1) * p)] * 1000
               for p in (.50, .95, .99)}, "max_ms": ordered[-1] * 1000}


async def child(args) -> dict:
    root = package_root(args.package)
    sys.path.insert(0, str(root))
    module = importlib.import_module("sao_noise.stream")
    if Path(module.__file__).resolve() != (root / "sao_noise/stream.py").resolve():
        raise RuntimeError("Imported a different package than requested")
    pcm = read_pcm(args.input)
    worker = module.RustDenoiser(args.executable.resolve(), atten_lim_db=args.attenuation_db)
    before = time.perf_counter()
    await worker.start(RATE)
    startup = time.perf_counter() - before
    delay = worker.stream_info.algorithmic_delay_samples
    chunks, records = [], []
    total_input = total_output = 0

    async def feed(data: bytes, phase: str) -> None:
        nonlocal total_input, total_output
        before = time.perf_counter()
        result = await worker.process(data)
        elapsed = time.perf_counter() - before
        total_input += len(data) // 2
        total_output += len(result) // 2
        if len(result) % (HOP * 2) or total_output != total_input // HOP * HOP:
            raise RuntimeError("Worker did not return the expected complete hops")
        chunks.append(result)
        records.append({"phase": phase, "input_samples": len(data) // 2,
                        "output_samples": len(result) // 2, "seconds": elapsed})

    try:
        for offset in range(0, len(pcm), args.packet_samples * 2):
            await feed(pcm[offset:offset + args.packet_samples * 2], "input")
        count = len(pcm) // 2
        # Include any partial input hop, then drain exactly enough model tail.
        tail = bytes((math.ceil((count + delay) / HOP) * HOP - count) * 2)
        for offset in range(0, len(tail), args.packet_samples * 2):
            await feed(tail[offset:offset + args.packet_samples * 2], "drain")
    finally:
        before = time.perf_counter()
        await worker.stop()
        shutdown = time.perf_counter() - before
    raw = b"".join(chunks)
    aligned = raw[delay * 2:delay * 2 + len(pcm)]
    if len(aligned) != len(pcm):
        raise RuntimeError("Output sample count changed after delay compensation")
    args.pcm_output.write_bytes(aligned)
    input_records = [r for r in records if r["phase"] == "input"]
    input_timings = [r["seconds"] for r in input_records]
    process_seconds = sum(input_timings)
    drain_seconds = sum(r["seconds"] for r in records if r["phase"] == "drain")
    return {
        "loaded_stream_sha256": digest(Path(module.__file__).read_bytes()),
        "loaded_binary_sha256": digest(args.executable.read_bytes()),
        "startup_seconds": startup, "shutdown_seconds": shutdown,
        "algorithmic_delay_samples": delay,
        "algorithmic_delay_ms": delay / RATE * 1000,
        "input_samples": len(pcm) // 2, "raw_output_samples": len(raw) // 2,
        "aligned_output_samples": len(aligned) // 2,
        "pcm_sha256": digest(aligned), "raw_pcm_sha256": digest(raw),
        "packet_samples": args.packet_samples,
        "process_seconds": process_seconds, "drain_seconds": drain_seconds,
        "real_time_factor": process_seconds / (len(pcm) / 2 / RATE),
        "real_time_factor_including_drain": (process_seconds + drain_seconds) / (len(pcm) / 2 / RATE),
        "real_time_factor_denominator_seconds": len(pcm) / 2 / RATE,
        "input_calls": distribution(input_timings),
        "input_calls_with_output": distribution([r["seconds"] for r in input_records if r["output_samples"]]),
        "input_calls_without_output": distribution([r["seconds"] for r in input_records if not r["output_samples"]]),
        "drain_calls": distribution([r["seconds"] for r in records if r["phase"] == "drain"]),
        "input_calls_over_packet_duration": sum(r["seconds"] > r["input_samples"] / RATE for r in input_records),
        "calls": records,
    }


def integers(pcm: bytes) -> array:
    result = array("h", pcm)
    if sys.byteorder != "little":
        result.byteswap()
    return result


def difference(baseline: bytes, candidate: bytes) -> dict:
    if len(baseline) != len(candidate):
        raise ValueError("Cannot compare PCM with different sample counts")
    deltas = [abs(a - b) for a, b in zip(integers(baseline), integers(candidate))]
    return {"exact_match": baseline == candidate,
            "different_samples": sum(d != 0 for d in deltas),
            "maximum_absolute_difference_pcm16": max(deltas, default=0),
            "mean_absolute_difference_pcm16": sum(deltas) / max(1, len(deltas)),
            "rms_difference_pcm16": math.sqrt(sum(d * d for d in deltas) / max(1, len(deltas)))}


def quality(reference: bytes, noisy: bytes, output: bytes) -> dict:
    clean, dry, wet = integers(reference), integers(noisy), integers(output)
    reference_power = sum(v * v for v in clean)
    if reference_power == 0:
        raise ValueError("Reference has no nonzero speech samples")
    def snr(measured):
        error = sum((a - b) ** 2 for a, b in zip(clean, measured))
        return 10 * math.log10(reference_power / max(error, 1e-20))
    # Complete 10 ms reference-silent windows avoid isolated waveform crossings.
    quiet = [i for start in range(0, len(clean) - HOP + 1, HOP)
             if max(abs(v) for v in clean[start:start + HOP]) <= 1
             for i in range(start, start + HOP)]
    dry_power, wet_power = sum(dry[i] ** 2 for i in quiet), sum(wet[i] ** 2 for i in quiet)
    input_snr, output_snr = snr(dry), snr(wet)
    return {"full_fixture_input_snr_db": input_snr,
            "full_fixture_output_snr_db": output_snr,
            "full_fixture_snr_change_db": output_snr - input_snr,
            "reference_silent_window_samples": len(quiet),
            "noise_only_reduction_db": 10 * math.log10(max(dry_power, 1e-20) / max(wet_power, 1e-20)) if quiet else None,
            "scope": "waveform SNR; 10 ms reference windows at <=1 PCM16 unit count as quiet; not perceptual quality or ASR accuracy"}


def run(args) -> dict:
    if not 1 <= args.runs <= 100:
        raise ValueError("runs must be between 1 and 100")
    if not math.isfinite(args.attenuation_db) or not 0 <= args.attenuation_db <= 100:
        raise ValueError("attenuation must be finite and between 0 and 100 dB")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    pcm = read_pcm(args.input)
    reference = read_pcm(args.reference) if args.reference else None
    if reference is not None and len(reference) != len(pcm):
        raise ValueError("Input and reference must have identical sample counts")
    variants = {}
    for name in ("baseline", "candidate"):
        executable = getattr(args, name + "_executable").resolve(strict=True)
        root = package_root(getattr(args, name + "_package"))
        variants[name] = {"executable": str(executable), "binary_sha256": digest(executable.read_bytes()),
                          "package_root": str(root), "stream_sha256": digest((root / "sao_noise/stream.py").read_bytes()),
                          "package_python_sha256": {str(p.relative_to(root)): digest(p.read_bytes())
                              for p in sorted((root / "sao_noise").rglob("*.py"))}}
    args.output.mkdir(parents=True, exist_ok=False)
    measurements = []
    script = Path(__file__).resolve()
    for iteration in range(args.runs):
        order = ("baseline", "candidate") if iteration % 2 == 0 else ("candidate", "baseline")
        for packet in PACKETS:
            for variant in order:
                entry = variants[variant]
                name = f"{variant}-packet{packet}-run{iteration + 1}"
                output = args.output / (name + ".pcm")
                command = [sys.executable, str(script), "--child", "--input", str(args.input.resolve()),
                           "--package", entry["package_root"], "--executable", entry["executable"],
                           "--packet-samples", str(packet), "--pcm-output", str(output.resolve()),
                           "--attenuation-db", str(args.attenuation_db)]
                before = time.perf_counter()
                completed = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout)
                wall = time.perf_counter() - before
                if completed.returncode:
                    raise RuntimeError(f"{name} failed: {completed.stderr[-4096:]}")
                result = json.loads(completed.stdout)
                if (result["loaded_stream_sha256"] != entry["stream_sha256"] or
                        result["loaded_binary_sha256"] != entry["binary_sha256"]):
                    raise RuntimeError("Package or binary changed during measurement; use stable copies")
                result.update(variant=variant, iteration=iteration + 1, measurement_order=len(measurements) + 1,
                              pcm_file=output.name, child_wall_seconds=wall)
                (args.output / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                measurements.append(result)
                print(f"{name}: {result['process_seconds']:.6f}s; p95 {result['input_calls']['p95_ms']:.4f}ms", file=sys.stderr, flush=True)
    comparisons, aggregates = [], {}
    for packet in PACKETS:
        group = [r for r in measurements if r["packet_samples"] == packet]
        first_baseline = next(r for r in group if r["variant"] == "baseline")
        baseline_pcm = (args.output / first_baseline["pcm_file"]).read_bytes()
        for result in group:
            result["parity_to_first_baseline"] = difference(baseline_pcm, (args.output / result["pcm_file"]).read_bytes())
            result["raw_pcm_exact_match_to_first_baseline"] = (
                result["raw_pcm_sha256"] == first_baseline["raw_pcm_sha256"] and
                result["raw_output_samples"] == first_baseline["raw_output_samples"])
            if result["algorithmic_delay_samples"] != first_baseline["algorithmic_delay_samples"]:
                raise RuntimeError("Model delay changed between variants; do not report a speed-only improvement")
            if reference is not None:
                result["quality"] = quality(reference, pcm, (args.output / result["pcm_file"]).read_bytes())
        for iteration in range(1, args.runs + 1):
            base = next(r for r in group if r["variant"] == "baseline" and r["iteration"] == iteration)
            candidate = next(r for r in group if r["variant"] == "candidate" and r["iteration"] == iteration)
            comparisons.append({"packet_samples": packet, "iteration": iteration,
                "baseline_process_seconds": base["process_seconds"], "candidate_process_seconds": candidate["process_seconds"],
                "processing_speedup_ratio": base["process_seconds"] / candidate["process_seconds"],
                "processing_time_reduction_percent": (1 - candidate["process_seconds"] / base["process_seconds"]) * 100,
                "raw_pcm_exact_match": (base["raw_pcm_sha256"] == candidate["raw_pcm_sha256"] and
                                        base["raw_output_samples"] == candidate["raw_output_samples"]),
                "pcm_difference": difference((args.output / base["pcm_file"]).read_bytes(), (args.output / candidate["pcm_file"]).read_bytes())})
        aggregates[str(packet)] = {}
        for variant in variants:
            rows = [r for r in group if r["variant"] == variant]
            input_calls = [c for r in rows for c in r["calls"] if c["phase"] == "input"]
            aggregates[str(packet)][variant] = {
                "runs": len(rows), "process_seconds_median": statistics.median(r["process_seconds"] for r in rows),
                "process_seconds_min": min(r["process_seconds"] for r in rows),
                "process_seconds_max": max(r["process_seconds"] for r in rows),
                "real_time_factor_median": statistics.median(r["real_time_factor"] for r in rows),
                "startup_seconds_median": statistics.median(r["startup_seconds"] for r in rows),
                "input_calls": distribution([c["seconds"] for c in input_calls]),
                "input_calls_with_output": distribution([c["seconds"] for c in input_calls if c["output_samples"]]),
                "input_calls_without_output": distribution([c["seconds"] for c in input_calls if not c["output_samples"]]),
                "all_outputs_exact_match_first_baseline": all(r["parity_to_first_baseline"]["exact_match"] for r in rows),
                "all_raw_outputs_exact_match_first_baseline": all(r["raw_pcm_exact_match_to_first_baseline"] for r in rows),
                "distinct_output_sha256": sorted({r["pcm_sha256"] for r in rows}),
            }
        pairs = [c for c in comparisons if c["packet_samples"] == packet]
        aggregates[str(packet)]["paired_speedup_ratio_median"] = statistics.median(c["processing_speedup_ratio"] for c in pairs)
        aggregates[str(packet)]["paired_time_reduction_percent_median"] = statistics.median(c["processing_time_reduction_percent"] for c in pairs)
    first = (args.output / measurements[0]["pcm_file"]).read_bytes()
    report = {"schema_version": 1, "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": {"system": platform.system(), "machine": platform.machine(), "processor": platform.processor(),
                     "python": platform.python_version()},
        "scope": "Sequential unpaced calls including IPC, inference and wrapper conversion; no warmup omitted; excludes startup, drain, microphone, network, real-time pacing and other application stages. Not end-to-end latency.",
        "run_order": "Each iteration uses baseline/candidate on odd iterations and candidate/baseline on even iterations, for 480 then 1536 samples. Fresh Python and native processes every measurement.",
        "sample_rate": RATE, "input_samples": len(pcm) // 2, "audio_seconds": len(pcm) / 2 / RATE,
        "input_wav_sha256": digest(args.input.read_bytes()),
        "reference_wav_sha256": digest(args.reference.read_bytes()) if args.reference else None,
        "attenuation_db": args.attenuation_db, "variants": variants,
        "alignment": "Native-reported delay removed; zero tail drained through complete 480-sample hops; original sample count retained.",
        "all_packetizations_and_variants_exact_match": all((args.output / r["pcm_file"]).read_bytes() == first for r in measurements),
        "all_raw_packetizations_and_variants_exact_match": all(
            r["raw_pcm_sha256"] == measurements[0]["raw_pcm_sha256"] and
            r["raw_output_samples"] == measurements[0]["raw_output_samples"] for r in measurements),
        "aggregates": aggregates, "paired_comparisons": comparisons, "measurements": measurements}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--attenuation-db", type=float, default=24)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--package", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--executable", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--pcm-output", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--packet-samples", type=int, choices=PACKETS, help=argparse.SUPPRESS)
    parser.add_argument("--baseline-executable", type=Path)
    parser.add_argument("--candidate-executable", type=Path)
    parser.add_argument("--baseline-package", type=Path)
    parser.add_argument("--candidate-package", type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", type=Path, help="New output directory, never overwritten")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=300, help="Maximum seconds per fresh-process measurement")
    args = parser.parse_args()
    required = ("package", "executable", "pcm_output", "packet_samples") if args.child else (
        "baseline_executable", "candidate_executable", "baseline_package", "candidate_package", "output")
    for name in required:
        if getattr(args, name) is None:
            parser.error("required argument: --" + name.replace("_", "-"))
    if args.child:
        print(json.dumps(asyncio.run(child(args))))
    else:
        report = run(args)
        print(json.dumps({"report": str((args.output / "report.json").resolve()),
                          "all_packetizations_and_variants_exact_match": report["all_packetizations_and_variants_exact_match"],
                          "all_raw_packetizations_and_variants_exact_match": report["all_raw_packetizations_and_variants_exact_match"],
                          "aggregates": report["aggregates"]}, indent=2))


if __name__ == "__main__":
    main()
