import unittest
from types import SimpleNamespace

import dialog_session
from audio_constants import ACTION_INDEX_BY_KEYWORD
from integrated_receiver import IntegratedReceiver


EXPECTED_ACTION_INDEX = {
    "left": 4,
    "right": 5,
    "wave": 7,
    "nod": 8,
    "shake": 10,
    "start": 11,
    "end": 12,
    "woshou": 13,
    "good": 14,
    "photo1": 15,
    "photo2": 16,
}


class FakeInt32:
    def __init__(self, data=0):
        self.data = data


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(msg)


class TestActionIndexMapping(unittest.TestCase):
    def test_mapping_matches_old_udp_receiver_contract(self):
        self.assertEqual(ACTION_INDEX_BY_KEYWORD, EXPECTED_ACTION_INDEX)

    def test_integrated_receiver_uses_same_mapping(self):
        receiver = IntegratedReceiver()
        try:
            for keyword, index in EXPECTED_ACTION_INDEX.items():
                self.assertEqual(receiver._keyword_to_index(keyword), index)
            self.assertIsNone(receiver._keyword_to_index("unknown"))
            self.assertIsNone(receiver._keyword_to_index(None))
        finally:
            receiver.stop()


class TestDialogSessionActionPublisher(unittest.TestCase):
    def setUp(self):
        self._old_int32 = dialog_session.Int32
        dialog_session.Int32 = FakeInt32

    def tearDown(self):
        dialog_session.Int32 = self._old_int32

    def _session_stub(self):
        return SimpleNamespace(
            action_index_pub=FakePublisher(),
            action_index_topic="/action_index",
        )

    def test_publish_wave_index(self):
        session = self._session_stub()
        published = dialog_session.DialogSession._emit_voice_keyword(session, "wave")
        self.assertTrue(published)
        self.assertEqual(len(session.action_index_pub.messages), 1)
        self.assertEqual(session.action_index_pub.messages[0].data, 7)

    def test_publish_nod_index(self):
        session = self._session_stub()
        published = dialog_session.DialogSession._emit_voice_keyword(session, "nod")
        self.assertTrue(published)
        self.assertEqual(session.action_index_pub.messages[0].data, 8)

    def test_publish_left_index(self):
        session = self._session_stub()
        published = dialog_session.DialogSession._emit_voice_keyword(session, "left")
        self.assertTrue(published)
        self.assertEqual(session.action_index_pub.messages[0].data, 4)

    def test_unknown_keyword_does_not_publish(self):
        session = self._session_stub()
        published = dialog_session.DialogSession._emit_voice_keyword(session, "unknown")
        self.assertFalse(published)
        self.assertEqual(session.action_index_pub.messages, [])

    def test_missing_publisher_does_not_publish(self):
        session = SimpleNamespace(action_index_pub=None, action_index_topic="/action_index")
        published = dialog_session.DialogSession._emit_voice_keyword(session, "wave")
        self.assertFalse(published)


if __name__ == "__main__":
    unittest.main()
