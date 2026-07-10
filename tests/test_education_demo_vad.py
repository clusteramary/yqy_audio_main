import asyncio
import unittest
from types import SimpleNamespace

import dialog_session


class TestEducationDemoVad(unittest.TestCase):
    def _session_stub(self):
        return SimpleNamespace(
            _script_waiting_for_user=False,
            _script_voice_active=False,
            _script_voice_candidate_since=None,
            _script_last_voice_ts=0.0,
            _script_user_started_event=asyncio.Event(),
            _script_user_finished_event=asyncio.Event(),
            _script_vad_threshold=800,
            _script_vad_start_sec=0.2,
            _script_vad_end_silence_sec=1.2,
        )

    def test_vad_detects_speech_start_and_silence_end(self):
        session = self._session_stub()
        dialog_session.DialogSession._prepare_script_user_turn(session)
        voice = (1200).to_bytes(2, "little", signed=True) * 160
        silence = b"\x00\x00" * 160

        dialog_session.DialogSession._process_script_vad_frame(
            session, voice, now=10.0
        )
        self.assertFalse(session._script_user_started_event.is_set())

        dialog_session.DialogSession._process_script_vad_frame(
            session, voice, now=10.21
        )
        self.assertTrue(session._script_user_started_event.is_set())
        self.assertFalse(session._script_user_finished_event.is_set())

        dialog_session.DialogSession._process_script_vad_frame(
            session, silence, now=11.42
        )
        self.assertTrue(session._script_user_finished_event.is_set())
        self.assertFalse(session._script_waiting_for_user)

    def test_short_noise_does_not_start_user_turn(self):
        session = self._session_stub()
        dialog_session.DialogSession._prepare_script_user_turn(session)
        voice = (1200).to_bytes(2, "little", signed=True) * 160
        silence = b"\x00\x00" * 160

        dialog_session.DialogSession._process_script_vad_frame(
            session, voice, now=20.0
        )
        dialog_session.DialogSession._process_script_vad_frame(
            session, silence, now=20.1
        )

        self.assertFalse(session._script_user_started_event.is_set())
        self.assertFalse(session._script_user_finished_event.is_set())

    def test_keepalive_sends_silence_while_script_is_running(self):
        class SessionStub:
            def __init__(self):
                self.is_running = True
                self.receive_error = None
                self.calls = 0

            async def _send_silence_if_due(self):
                self.calls += 1
                self.is_running = False

        session = SessionStub()

        asyncio.run(
            dialog_session.DialogSession._script_audio_keepalive_loop(session)
        )

        self.assertEqual(session.calls, 1)
        self.assertIsNone(session.receive_error)


if __name__ == "__main__":
    unittest.main()
