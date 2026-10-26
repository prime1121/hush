//! Stateful mono, full-band speech enhancement backed by the authors' DFN3 runtime.
//! Each input stream must own a separate instance. Input/output are normalized PCM.
use anyhow::{ensure, Context, Result};
use df::tract::{DfParams, DfTract, RuntimeParams};
use ndarray::{ArrayView2, ArrayViewMut2};

pub const SAMPLE_RATE: u32 = 48_000;

/// A single speech stream. The underlying runtime retains recurrent and DSP state.
pub struct Denoiser {
    model: DfTract,
}

impl Denoiser {
    /// Limit suppression to `atten_lim_db` (0 = dry, 100 = unlimited suppression).
    /// The conservative default used by the CLI is 24 dB. Construct off the audio thread.
    pub fn new(atten_lim_db: f32) -> Result<Self> {
        ensure!(
            atten_lim_db.is_finite() && (0.0..=100.0).contains(&atten_lim_db),
            "attenuation must be finite and between 0 and 100 dB"
        );
        let params = RuntimeParams::default_with_ch(1).with_atten_lim(atten_lim_db);
        let model = DfTract::new(DfParams::default(), &params)
            .context("could not initialize embedded DeepFilterNet3 model")?;
        ensure!(model.sr == SAMPLE_RATE as usize, "expected a 48 kHz model");
        ensure!(model.hop_size == 480, "expected a 10 ms model hop");
        ensure!(model.ch == 1, "expected a mono model");
        Ok(Self { model })
    }

    pub fn frame_samples(&self) -> usize {
        self.model.hop_size
    }

    /// Signal alignment delay, excluding the time required to collect the current hop.
    /// For the pinned DFN3 model this is 1,440 samples (30 ms); frame acquisition adds
    /// up to 10 ms, corresponding to the paper's 40 ms algorithmic latency.
    pub fn delay_samples(&self) -> usize {
        self.model.fft_size - self.model.hop_size + self.model.lookahead * self.model.hop_size
    }

    /// Process exactly one hop. Invalid input is rejected before state is advanced.
    /// A runtime error makes this stream unusable; construct a fresh instance to reset.
    pub fn process_frame(&mut self, input: &[f32], output: &mut [f32]) -> Result<()> {
        let hop = self.frame_samples();
        ensure!(
            input.len() == hop && output.len() == hop,
            "expected {hop} samples per frame"
        );
        ensure!(
            input.iter().all(|v| v.is_finite() && v.abs() <= 1.0),
            "input must be finite normalized PCM in [-1, 1]"
        );
        let input = ArrayView2::from_shape((1, hop), input)?;
        let target = ArrayViewMut2::from_shape((1, hop), &mut *output)?;
        self.model
            .process(input, target)
            .context("DeepFilterNet3 inference failed")?;
        ensure!(
            output.iter().all(|v| v.is_finite()),
            "model produced non-finite output"
        );
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stft_reset_and_clone_preserve_stream_isolation() {
        let mut reused = df::DFState::default();
        let mut fresh = df::DFState::default();
        let mut reused_output = vec![0.0; reused.frame_size];
        let mut fresh_output = vec![0.0; fresh.frame_size];
        let warmup: Vec<f32> = (0..reused.frame_size)
            .map(|i| (i as f32 * 0.13).sin() * 0.4)
            .collect();
        for _ in 0..7 {
            reused.process_frame(&warmup, &mut reused_output);
        }
        // Reused FFT workspaces retain their old contents across reset. None of
        // those samples may leak into a newly reset stream.
        reused.reset();
        for frame in 0..9 {
            let input: Vec<f32> = (0..reused.frame_size)
                .map(|i| ((frame * reused.frame_size + i) as f32 * 0.07).sin() * 0.2)
                .collect();
            reused.process_frame(&input, &mut reused_output);
            fresh.process_frame(&input, &mut fresh_output);
            assert_eq!(reused_output, fresh_output);
        }

        let mut fork = reused.clone();
        let mut fork_output = vec![0.0; fork.frame_size];
        for _ in 0..4 {
            reused.process_frame(&warmup, &mut reused_output);
            fork.process_frame(&warmup, &mut fork_output);
            assert_eq!(reused_output, fork_output);
        }
    }

    #[test]
    fn rejects_invalid_attenuation_before_loading() {
        for value in [-1.0, 101.0, f32::NAN, f32::INFINITY] {
            assert!(Denoiser::new(value).is_err());
        }
    }

    #[test]
    fn real_model_framing_silence_and_validation() -> Result<()> {
        let mut denoiser = Denoiser::new(24.0)?;
        assert_eq!(denoiser.frame_samples(), 480);
        assert_eq!(denoiser.delay_samples(), 1440);
        let mut out = [0.0; 480];
        assert!(denoiser.process_frame(&[0.0; 479], &mut out).is_err());
        assert!(denoiser.process_frame(&[f32::NAN; 480], &mut out).is_err());
        assert!(denoiser.process_frame(&[1.1; 480], &mut out).is_err());
        for _ in 0..8 {
            denoiser.process_frame(&[0.0; 480], &mut out)?;
            assert!(out.iter().all(|x| x.abs() < 1e-7));
        }
        Ok(())
    }

    #[test]
    fn dry_mode_preserves_signal_at_documented_delay() -> Result<()> {
        let mut denoiser = Denoiser::new(0.0)?;
        let n = 480 * 12;
        let source: Vec<f32> = (0..n)
            .map(|i| {
                let amplitude = match i / 480 {
                    2..=4 => 0.0,    // A pause must not freeze or compress the time line.
                    5..=7 => 0.0001, // Below upstream's former silence threshold.
                    _ => 0.25,
                };
                amplitude * (i as f32 * 0.07).sin()
            })
            .collect();
        let delay = denoiser.delay_samples();
        let mut padded = source.clone();
        padded.resize(n + delay, 0.0);
        let mut output = vec![0.0; padded.len()];
        for (input, out) in padded.chunks(480).zip(output.chunks_mut(480)) {
            denoiser.process_frame(input, out)?;
        }
        let max_error = source
            .iter()
            .zip(&output[delay..])
            .map(|(a, b)| (a - b).abs())
            .fold(0.0_f32, f32::max);
        assert!(max_error < 1e-6, "dry alignment error {max_error}");
        Ok(())
    }

    #[test]
    fn enhanced_tail_is_emitted_during_zero_padding() -> Result<()> {
        let mut denoiser = Denoiser::new(24.0)?;
        let input: Vec<f32> = (0..480).map(|i| 0.25 * (i as f32 * 0.08).sin()).collect();
        let mut output = [0.0; 480];
        denoiser.process_frame(&input, &mut output)?;
        let mut tail_energy = 0.0;
        for _ in 0..3 {
            denoiser.process_frame(&[0.0; 480], &mut output)?;
            tail_energy += output.iter().map(|x| x * x).sum::<f32>();
        }
        assert!(
            tail_energy > 1e-7,
            "zero padding discarded the delayed signal"
        );
        Ok(())
    }
}
