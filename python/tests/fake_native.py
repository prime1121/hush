"""Deterministic protocol fixture; this is NOT a noise suppression model."""

import json
import sys
import time

mode = sys.argv[1]
if sys.argv[2:4] != ["stream", "--atten-lim-db"]:
    sys.exit("invalid command")
if mode == "startup_timeout":
    time.sleep(30)
if mode == "bad_header":
    sys.stdout.buffer.write(b'{"protocol":2}\n')
    sys.stdout.buffer.flush()
    time.sleep(30)
    sys.exit()
if mode == "early_exit":
    sys.stderr.buffer.write(b"model unavailable\n")
    sys.exit(5)

delay_samples = 1440 if mode == "delay" else 0
header = dict(protocol=1, sample_rate=48000, frame_samples=480,
              algorithmic_delay_samples=delay_samples, model="DeepFilterNet3")
sys.stdout.buffer.write(json.dumps(header).encode() + b"\n")
sys.stdout.buffer.flush()
delayed = bytearray(delay_samples * 2)
if mode == "stderr_flood":
    sys.stderr.buffer.write(b"diagnostic\n" * 100_000)
    sys.stderr.buffer.flush()
if mode == "stdout_flood":
    sys.stdout.buffer.write(bytes(10_000_000))
    sys.stdout.buffer.flush()
while True:
    frame = sys.stdin.buffer.read(960)
    if not frame:
        break
    if len(frame) != 960:
        sys.exit("partial frame")
    if mode == "response_timeout":
        time.sleep(30)
    if mode == "short_response":
        sys.stdout.buffer.write(frame[:16])
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(b"native crashed\n")
        sys.exit(7)
    wet = bytes(value ^ 0x55 for value in frame)
    delayed.extend(wet)
    output = delayed[:960]
    del delayed[:960]
    # Deliberately split writes; readexactly must assemble the frame.
    sys.stdout.buffer.write(output[:100])
    sys.stdout.buffer.flush()
    sys.stdout.buffer.write(output[100:])
    sys.stdout.buffer.flush()
