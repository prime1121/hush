# Contributing to Hush

Bug fixes, integration improvements and reproducible audio benchmarks are
welcome. For a substantial API or model change, open an issue before starting
so the design can be discussed.

## Development setup

Follow the build and Python environment setup in [README.md](README.md).
Install the Pipecat extra for integration tests and Node.js to run browser
capture/playback tests. Set `SAO_DENOISE_EXE` to the absolute binary produced
by your build, then run the verification commands in the README.
Use `python tools/verify.py` for the same complete check as CI.

Keep recordings, credentials, machine-specific settings and generated reports
outside Git. Use `scripts/generate_fixture.py` for reproducible tests; its sample
does not contain intelligible speech.

## Changes

- Keep each pull request focused on one problem. Explain the resulting behavior
  and the checks you ran.
- Preserve the `sao_noise` Python API and `sao-denoise` stream protocol, or discuss
  a migration before changing them.
- Use descriptive names and small functions. Add comments for timing, protocol
  constraints or non-obvious decisions; avoid restating the code.
- Keep generated files, virtual environments, compiled binaries and audio clips
  out of commits. Do not include secrets in logs, tests or examples.
- Preserve vendor licenses and attribution. Document vendor modifications in
  `vendor/PROVENANCE.md`; do not reformat unrelated upstream code.

## Audio and performance checks

A change to framing, buffering, state or shutdown should include a regression
for the affected behavior. Check silent gaps, quiet input, partial hops, dry/wet
toggles and trailing audio when relevant. Preserve bounded queues and clean up
worker processes after cancellation.

For performance changes, compare the same input and configuration with repeated
runs. Report hardware, build settings, sample rate, packet size and timing scope.
Separate startup, inference and transport latency. Check PCM parity when the
change should preserve output. A synthetic fixture cannot establish speech
quality or overlapping-speaker separation.

Share only audio you have permission to redistribute. A private recording can
be used for local evaluation without attaching it to an issue or pull request.

## Reporting a bug

Include the operating system, Rust/Python versions, exact command, relevant
settings and a minimal reproduction. Trim logs to the relevant error and remove
credentials and personal data. Describe expected and actual behavior, especially
sample counts or timestamps when reporting an audio discontinuity.

Contributions are distributed under the repository's [license](LICENSE).
