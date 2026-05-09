import queue
import time
import unittest

import dialog_session


class FakeStream:
    def __init__(self):
        self.active = True
        self.started = 0
        self.stopped = 0

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False
        self.stopped += 1

    def start_stream(self):
        self.active = True
        self.started += 1


class TestHalfDuplexMicGate(unittest.TestCase):
    def _session_stub(self):
        class SessionStub:
            _drain_ros_audio_queue = dialog_session.DialogSession._drain_ros_audio_queue
            _set_input_stream_active = dialog_session.DialogSession._set_input_stream_active
            _pause_half_duplex_mic = dialog_session.DialogSession._pause_half_duplex_mic
            _resume_half_duplex_mic = dialog_session.DialogSession._resume_half_duplex_mic
            _hold_half_duplex_mic_if_needed = (
                dialog_session.DialogSession._hold_half_duplex_mic_if_needed
            )

        q = queue.Queue()
        q.put_nowait(b"echo-1")
        q.put_nowait(b"echo-2")
        stream = FakeStream()
        sent_commands = []

        def send_mic_command(command, cooldown_sec=0.2):
            sent_commands.append((command, cooldown_sec))

        session = SessionStub()
        session.block_mic_while_playing = True
        session._half_duplex_mic_paused = False
        session._half_duplex_resume_after = 0.0
        session._half_duplex_resume_delay_sec = 0.05
        session.ros_audio_queue = q
        session.input_stream = stream
        session.send_mic_command = send_mic_command
        session._is_tts_playing = lambda: False
        session.sent_commands = sent_commands
        return session

    def test_pause_stops_input_stream_drains_ros_queue_and_sends_close_command(self):
        session = self._session_stub()

        session._pause_half_duplex_mic("test")

        self.assertTrue(session._half_duplex_mic_paused)
        self.assertFalse(session.input_stream.active)
        self.assertEqual(session.input_stream.stopped, 1)
        self.assertTrue(session.ros_audio_queue.empty())
        self.assertEqual(session.sent_commands, [("send_microphone", 0.0)])

    def test_resume_starts_input_stream_drains_ros_queue_and_sends_open_command(self):
        session = self._session_stub()
        session._pause_half_duplex_mic("test")
        session.ros_audio_queue.put_nowait(b"tail-echo")

        session._resume_half_duplex_mic("done")

        self.assertFalse(session._half_duplex_mic_paused)
        self.assertTrue(session.input_stream.active)
        self.assertEqual(session.input_stream.started, 1)
        self.assertTrue(session.ros_audio_queue.empty())
        self.assertEqual(
            session.sent_commands,
            [("send_microphone", 0.0), ("release_microphone", 0.0)],
        )

    def test_hold_keeps_mic_paused_during_tail_delay(self):
        session = self._session_stub()
        session._pause_half_duplex_mic("test")
        session._half_duplex_resume_after = time.time() + 1.0

        held = session._hold_half_duplex_mic_if_needed()

        self.assertTrue(held)
        self.assertTrue(session._half_duplex_mic_paused)
        self.assertEqual(session.sent_commands, [("send_microphone", 0.0)])


if __name__ == "__main__":
    unittest.main()
