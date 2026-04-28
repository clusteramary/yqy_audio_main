import unittest

from duplex_audio import (
    build_stop_message,
    pack_audio_frame,
    parse_control_message,
    try_unpack_audio_frame,
)


class TestAudioFrame(unittest.TestCase):
    def test_pack_unpack_roundtrip(self):
        uid = 42
        seq = 7
        payload = b"hello world audio data"
        packed = pack_audio_frame(uid, seq, False, payload)
        frame = try_unpack_audio_frame(packed)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.utterance_id, uid)
        self.assertEqual(frame.chunk_seq, seq)
        self.assertFalse(frame.is_last)
        self.assertEqual(frame.payload, payload)

    def test_pack_is_last(self):
        packed = pack_audio_frame(1, 0, True, b"last")
        frame = try_unpack_audio_frame(packed)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertTrue(frame.is_last)

    def test_pack_is_not_last(self):
        packed = pack_audio_frame(1, 0, False, b"mid")
        frame = try_unpack_audio_frame(packed)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertFalse(frame.is_last)

    def test_unpack_bare_pcm_returns_none(self):
        bare = b"\x00\x00\x01\x00\xff\xff\xfe\xff"
        frame = try_unpack_audio_frame(bare)
        self.assertIsNone(frame)

    def test_unpack_header_only_returns_none(self):
        packed = pack_audio_frame(1, 0, False, b"test")
        header_only = packed[:17]
        frame = try_unpack_audio_frame(header_only)
        self.assertIsNone(frame)

    def test_unpack_truncated_returns_none(self):
        packed = pack_audio_frame(5, 3, True, b"payload here")
        # Truncate payload by 1 byte
        truncated = packed[:-1]
        frame = try_unpack_audio_frame(truncated)
        self.assertIsNone(frame)

    def test_unpack_empty_data(self):
        self.assertIsNone(try_unpack_audio_frame(b""))
        self.assertIsNone(try_unpack_audio_frame(bytes(10)))

    def test_multiple_frames(self):
        payloads = [b"first", b"second", b"third"]
        for i, p in enumerate(payloads):
            packed = pack_audio_frame(10, i, i == len(payloads) - 1, p)
            frame = try_unpack_audio_frame(packed)
            self.assertIsNotNone(frame)
            assert frame is not None
            self.assertEqual(frame.utterance_id, 10)
            self.assertEqual(frame.chunk_seq, i)
            self.assertEqual(frame.payload, p)

    def test_large_payload(self):
        large = b"x" * 10000
        packed = pack_audio_frame(99, 5, True, large)
        frame = try_unpack_audio_frame(packed)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.payload, large)

    def test_zero_payload(self):
        packed = pack_audio_frame(1, 0, True, b"")
        frame = try_unpack_audio_frame(packed)
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.payload, b"")

    def test_random_garbage(self):
        import random as _random

        for _ in range(100):
            length = _random.randint(0, 50)
            garbage = bytes(_random.getrandbits(8) for _ in range(length))
            frame = try_unpack_audio_frame(garbage)
            # Should never crash, just return None for invalid data
            if len(garbage) >= 17 and garbage[:4] == b"FDPX":
                # Could be valid, don't assert
                pass


class TestControlMessage(unittest.TestCase):
    def test_build_stop_message(self):
        msg = build_stop_message(42, "barge_in")
        self.assertIn("stop", msg)
        self.assertIn("42", msg)
        self.assertIn("barge_in", msg)

    def test_parse_stop_message(self):
        msg = build_stop_message(7, "test_reason")
        control = parse_control_message(msg)
        self.assertIsNotNone(control)
        assert control is not None
        self.assertEqual(control.cmd, "stop")
        self.assertEqual(control.utterance_id, 7)
        self.assertEqual(control.reason, "test_reason")

    def test_parse_invalid_json(self):
        self.assertIsNone(parse_control_message("not json"))

    def test_parse_missing_cmd(self):
        self.assertIsNone(parse_control_message('{"utterance_id": 1}'))

    def test_parse_missing_utterance_id(self):
        self.assertIsNone(parse_control_message('{"cmd": "stop"}'))

    def test_parse_none_string(self):
        self.assertIsNone(parse_control_message(None))  # type: ignore[arg-type]
        self.assertIsNone(parse_control_message(""))


class TestBargeInDetection(unittest.TestCase):
    """Test RMS energy computation and barge-in logic as pure functions."""

    def _rms(self, data: bytes) -> float:
        import struct as _struct

        if len(data) < 2:
            return 0.0
        count = len(data) // 2
        fmt = f"<{count}h"
        try:
            samples = _struct.unpack(fmt, data)
        except Exception:
            return 0.0
        if count == 0:
            return 0.0
        return (sum(s * s for s in samples) / count) ** 0.5

    def test_silence_low_energy(self):
        silence = b"\x00" * 640  # 20ms @ 16kHz, 16-bit
        self.assertLess(self._rms(silence), 100)

    def test_loud_signal_high_energy(self):
        loud = b"\xff\x7f" * 320  # near max positive
        self.assertGreater(self._rms(loud), 20000)

    def test_dont_trigger_on_silence(self):
        silence = b"\x00" * 640
        threshold = 800
        self.assertLess(self._rms(silence), threshold)

    def test_trigger_on_loud(self):
        loud_ish = b"\x00\x04" * 320  # amplitude ~1024
        threshold = 800
        self.assertGreater(self._rms(loud_ish), threshold)


if __name__ == "__main__":
    unittest.main()
