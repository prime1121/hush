"""Runs against the real optional Pipecat dependency when installed."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

HAS_PIPECAT = importlib.util.find_spec("pipecat") is not None


@unittest.skipUnless(HAS_PIPECAT, "install sao-noise[pipecat] for Pipecat integration tests")
class PipecatTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_lifecycle_and_control_frames(self):
        from pipecat.audio.filters.base_audio_filter import BaseAudioFilter
        from pipecat.frames.frames import FilterControlFrame, FilterEnableFrame
        from sao_noise.pipecat import DeepFilterNetFilter

        fake = str(Path(__file__).with_name("fake_native.py"))
        audio_filter = DeepFilterNetFilter([sys.executable, fake, "normal"])
        self.addAsyncCleanup(audio_filter.stop)
        self.assertIsInstance(audio_filter, BaseAudioFilter)
        await audio_filter.start(48000)
        await audio_filter.process_frame(FilterControlFrame())
        self.assertEqual(await audio_filter.filter(bytes(960)), b"\x55" * 960)
        await audio_filter.process_frame(FilterEnableFrame(enable=False))
        self.assertEqual(await audio_filter.filter(bytes(960)), bytes(960))
        await audio_filter.process_frame(FilterEnableFrame(enable=True))
        self.assertEqual(await audio_filter.filter(bytes(960)), b"\x55" * 960)
        await audio_filter.stop()
        self.assertFalse(audio_filter.denoiser.running)

    async def test_real_input_transport_routes_enabled_settings(self):
        from pipecat.frames.frames import FilterUpdateSettingsFrame
        from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
        from pipecat.transports.base_input import BaseInputTransport
        from pipecat.transports.base_transport import TransportParams
        from sao_noise.pipecat import DeepFilterNetFilter

        fake = str(Path(__file__).with_name("fake_native.py"))
        audio_filter = DeepFilterNetFilter([sys.executable, fake, "normal"])
        self.addAsyncCleanup(audio_filter.stop)
        await audio_filter.start(48000)
        transport = BaseInputTransport(TransportParams(
            audio_in_filter=audio_filter, audio_in_sample_rate=48000, audio_in_channels=1,
        ))
        # Skip only unrelated pipeline scheduling/setup hooks. The real
        # BaseInputTransport.process_frame dispatch and actual filter run here.
        with (patch.object(FrameProcessor, "process_frame", new_callable=AsyncMock) as parent,
              patch.object(transport, "push_frame", new_callable=AsyncMock) as pushed):
            await transport.process_frame(
                FilterUpdateSettingsFrame(settings={"enabled": False}), FrameDirection.UPSTREAM,
            )
            self.assertFalse(audio_filter.denoiser.enabled)
            self.assertEqual(await audio_filter.filter(bytes(960)), bytes(960))
            await transport.process_frame(
                FilterUpdateSettingsFrame(settings={"enabled": True}), FrameDirection.UPSTREAM,
            )
            self.assertTrue(audio_filter.denoiser.enabled)
            self.assertEqual(await audio_filter.filter(bytes(960)), b"\x55" * 960)
            with self.assertRaisesRegex(TypeError, "bool"):
                await transport.process_frame(
                    FilterUpdateSettingsFrame(settings={"enabled": "false"}), FrameDirection.UPSTREAM,
                )
            self.assertTrue(audio_filter.denoiser.enabled)
            await transport.process_frame(
                FilterUpdateSettingsFrame(settings={"unrelated_setting": 1}), FrameDirection.UPSTREAM,
            )
            self.assertTrue(audio_filter.denoiser.enabled)
            self.assertEqual(parent.await_count, 4)
            pushed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
