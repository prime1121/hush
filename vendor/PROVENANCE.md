# DeepFilterNet dependency

Vendored from the authors' repository at
https://github.com/Rikorose/DeepFilterNet/tree/978576aa8400552a4ce9730838c635aa30db5e61
(v0.5.6). `libDF/`, its license files, the upstream README, and the normal
DeepFilterNet3 ONNX bundle were copied from this revision. Only the `tract` and
`default-model` features are supported by this package; other model bundles and
training assets are intentionally absent.

Model file: `models/DeepFilterNet3_onnx.tar.gz`, 7,983,136 bytes.
SHA-256: `c94d91f70911001c946e0fabb4aa9adc37045f45a03b56008cb0c8244cb63616`.
The weights are unchanged and embedded at compile time. Inference needs no
network access, external model server, PyTorch, or GPU.

The upstream code offers MIT or Apache-2.0 licensing. Original notices and both
licenses are retained under `libDF/`. The pretrained bundle comes from the same
repository; the [authors' paper](https://arxiv.org/abs/2305.08227) explicitly
describes the code and pretrained weights as open source. Transitive dependency
licenses still apply when distributing binaries.

Local changes are marked `SAO patch`:

1. Removed the low-energy early return from `DfTract::process`. Silent hops must
   advance analysis, normalization, encoder, lookahead and synthesis state so
   pauses retain timing and the delayed speech tail can drain.
2. Removed the immediate dry-audio return at zero attenuation. The existing
   delayed spectral blend now provides dry output with consistent alignment.
3. At an exactly dry blend, assign the noisy spectrum directly to avoid `0 * NaN`.

Changes 1–3 are in `libDF/src/tract.rs`. In `libDF/src/lib.rs`, `DFState`
also retains its forward-FFT input and inverse-FFT output workspaces instead of
allocating both on every hop. Both buffers are completely overwritten before
their previous contents can be read. Each stream owns its buffers, and cloning
the state copies them. This removes two allocations per hop while retaining
about 7.5 KiB per mono stream with the pinned 960-sample FFT.

The workspace reuse changes no arithmetic, model weights, suppression settings,
hop size or lookahead. All silent frames still advance state. The signal delay
remains 1,440 samples (30 ms) at 48 kHz, excluding acquisition of a 10 ms hop.
The existing inverse-FFT `InputValues` warning handling is unchanged; that FFT
implementation writes the output before returning the warning.

The parent crate's regression tests exercise silence gaps, low-amplitude input,
30 ms dry-signal alignment and tail draining. The parent crate's
`stft_reset_and_clone_preserve_stream_isolation` test additionally compares a
reset stream with a fresh stream and checks cloned-state continuation. Verify
the deployed build against the prior build with the same audio fixture before
claiming a speed improvement. No model retraining or architectural novelty is
claimed.
