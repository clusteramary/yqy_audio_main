import unittest

from duplex_audio import (
    build_stop_message,
    pack_audio_frame,
    parse_control_message,
    try_unpack_audio_frame,
)


class DuplexAudioProtocolTest(unittest.TestCase):
    def test_audio_frame_round_trip(self):
        packed = pack_audio_frame(42, 7, True, b"pcm-data")
        frame = try_unpack_audio_frame(packed)

        self.assertIsNotNone(frame)
        self.assertEqual(frame.utterance_id, 42)
        self.assertEqual(frame.chunk_seq, 7)
        self.assertTrue(frame.is_last)
        self.assertEqual(frame.payload, b"pcm-data")

    def test_bare_or_truncated_pcm_is_not_a_frame(self):
        self.assertIsNone(try_unpack_audio_frame(b"\x00\x01\x02\x03"))
        self.assertIsNone(
            try_unpack_audio_frame(pack_audio_frame(1, 0, False, b"abc")[:-1])
        )

    def test_stop_message_round_trip(self):
        control = parse_control_message(build_stop_message(9, "test"))

        self.assertIsNotNone(control)
        self.assertEqual(control.cmd, "stop")
        self.assertEqual(control.utterance_id, 9)
        self.assertEqual(control.reason, "test")


if __name__ == "__main__":
    unittest.main()
