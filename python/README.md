# Hush Python integration

The compatible package name is `sao-noise`, with imports under `sao_noise`.
Inference runs in a persistent Rust `sao-denoise` subprocess. Build the engine
first; Python does not download or load a separate neural model.

From the product directory:

```console
python -m pip install -e "./python[pipecat]"
```

Omit `[pipecat]` for the standalone asynchronous client.

```python
from sao_noise.pipecat import DeepFilterNetFilter

noise_filter = DeepFilterNetFilter(
    executable="/absolute/path/to/sao-denoise",  # .exe on Windows
    atten_lim_db=24.0,
)
# Configure your Pipecat transport:
# audio_in_enabled=True, audio_in_sample_rate=48000, audio_in_channels=1,
# audio_in_filter=noise_filter
# Also set PipelineParams(audio_in_sample_rate=48000).
```

For direct use, call `await start(48000)`, `await process(pcm_bytes)`, and
`await stop()` in `finally` on `sao_noise.RustDenoiser`. Use a separate instance
per input stream. Input must be mono PCM16 little-endian at 48 kHz; raw bytes
cannot reveal stereo, so channel count must be configured upstream.

## Framing and controls

Input may contain any number of complete two-byte samples. Partial 480-sample
hops are buffered; an output call can return fewer bytes than its input, or none.
Up to four available hops are exchanged per bounded IPC call (3,840 bytes),
without waiting to fill a batch. No audio is silently dropped to match lengths.

Startup metadata reports 1,440 samples / 30 ms signal delay. Frame acquisition
adds up to 10 ms; IPC, processing, scheduling and transport add their own delay.
Initial delayed samples remain in live output.

Send `FilterUpdateSettingsFrame(settings={"enabled": False})` upstream to the
Pipecat input transport to select delayed dry audio; use `True` to restore it.
Values must be actual booleans. `FilterEnableFrame` is also understood when
calling the filter directly, but Pipecat 1.12 does not route it to the input
filter. The integration baseline is Pipecat 1.12.0.

The model continues processing while disabled, so bypass does not save CPU.
Control modes follow input samples through the delay; toggles preserve order
and buffered state. Instant wet/dry switching has no crossfade and may click.

`stop()` discards incomplete input and delayed tail because Pipecat cleanup
cannot emit audio. Preserve at least 40 ms trailing silence when the last speech
samples matter. Restart creates fresh state and re-enables enhancement.

## Failures and lifecycle

Startup timeout defaults to 60 s; each bounded exchange to 5 s; shutdown to 2 s
before forced termination. These values are configurable. Stdout carries only
startup JSON followed by PCM. Stderr is continuously drained with its latest
8 KiB retained for bounded diagnostics. No shell launches the worker.

Cancellation or stream failure closes and invalidates the native session;
explicitly restart before submitting more audio. Unreturned audio from a
cancelled call is discarded. Errors never silently bypass enhancement. Calls
before startup, duplicate startup and odd-length PCM raise errors.

On Windows use Python's default Proactor event loop; Selector loops do not
support the required subprocess transport.

## Tests

From the product directory:

```console
python -m unittest discover -s python/tests -v
```

Set `SAO_DENOISE_EXE` to the absolute built binary and install `[pipecat]` to
include actual-model tests. A configured invalid path fails rather than skips.
The protocol fixture transforms bytes and is not a neural quality simulation.
Native checks verify real model framing, silence, delayed toggles, restart
determinism and streaming/WAV PCM parity. These are engineering regressions,
not subjective quality or overlapping-speaker isolation tests.
