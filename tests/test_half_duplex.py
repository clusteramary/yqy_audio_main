import queue
import time
import unittest

from dialog_session import DialogSession


class FakeInputStream:
    def __init__(self):
        self.active = True
        self.starts = 0
        self.stops = 0

    def is_active(self):
        return self.active

    def start_stream(self):
        self.active = True
        self.starts += 1

    def stop_stream(self):
        self.active = False
        self.stops += 1


class HalfDuplexMicGateTest(unittest.TestCase):
    def make_session_stub(self):
        class Stub:
            _drain_audio_input_queue = DialogSession._drain_audio_input_queue
            _mic_frame_blocked = DialogSession._mic_frame_blocked
            _pause_half_duplex_mic = DialogSession._pause_half_duplex_mic
            _resume_half_duplex_mic = DialogSession._resume_half_duplex_mic
            _hold_half_duplex_mic_if_needed = (
                DialogSession._hold_half_duplex_mic_if_needed
            )

        commands = []
        session = Stub()
        session.block_mic_while_playing = True
        session._half_duplex_mic_paused = False
        session._half_duplex_resume_after = 0.0
        session._half_duplex_resume_delay_sec = 0.05
        session.ros_audio_queue = queue.Queue()
        session.ros_audio_queue.put_nowait(b"speaker-echo")
        session.input_stream = FakeInputStream()
        session.send_mic_command = (
            lambda command, cooldown_sec=0.2: commands.append(
                (command, cooldown_sec)
            )
        )
        session._is_tts_playing = lambda: False
        session.commands = commands
        return session

    def test_pause_blocks_frames_without_cross_thread_stream_stop(self):
        session = self.make_session_stub()

        session._pause_half_duplex_mic("test")

        self.assertTrue(session._half_duplex_mic_paused)
        self.assertTrue(session._mic_frame_blocked())
        self.assertTrue(session.input_stream.active)
        self.assertEqual(session.input_stream.stops, 0)
        self.assertTrue(session.ros_audio_queue.empty())
        self.assertEqual(session.commands, [("send_microphone", 0.0)])

    def test_resume_waits_for_configured_tail_delay(self):
        session = self.make_session_stub()
        session._pause_half_duplex_mic("test")
        session._half_duplex_resume_after = time.time() + 0.5

        self.assertTrue(session._hold_half_duplex_mic_if_needed())
        self.assertTrue(session._mic_frame_blocked())

        session._half_duplex_resume_after = time.time() - 0.01
        self.assertFalse(session._hold_half_duplex_mic_if_needed())
        self.assertFalse(session._mic_frame_blocked())
        self.assertTrue(session.input_stream.active)
        self.assertEqual(
            session.commands,
            [("send_microphone", 0.0), ("release_microphone", 0.0)],
        )

    def test_full_duplex_never_pauses_input(self):
        session = self.make_session_stub()
        session.block_mic_while_playing = False
        session._is_tts_playing = lambda: True

        self.assertFalse(session._hold_half_duplex_mic_if_needed())
        self.assertTrue(session.input_stream.active)
        self.assertEqual(session.commands, [])

    def test_frame_started_while_paused_is_discarded_after_resume(self):
        session = self.make_session_stub()
        session._half_duplex_mic_paused = False

        self.assertTrue(session._mic_frame_blocked(paused_before_read=True))


if __name__ == "__main__":
    unittest.main()
