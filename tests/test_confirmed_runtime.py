import unittest
from interview_robot.confirmed_runtime import ConfirmedInterviewController
from interview_robot.ranking import LexicalRanker
from interview_robot.state import InteractionState


class ConfirmedTests(unittest.TestCase):
    def setUp(self):
        self.c = ConfirmedInterviewController('s', LexicalRanker(), start_timeout=2, finish_timeout=3)
        self.q = [{'id': 'a', 'text': 'career?'}, {'id': 'b', 'text': 'growth?'}]

    def step(self, t, u='u', turn='SHIFT'):
        s = InteractionState('s', u, t, None, turn, 'unknown', (), {}, 'fixture')
        return self.c.step(s, ['career'], [], self.q)

    def test_normal_ack_cooldown_and_exactly_once(self):
        ask = self.step(0)
        self.assertEqual(ask['action'], 'ASK')
        self.assertEqual(self.step(.1)['action'], 'WAIT')
        self.assertTrue(self.c.feedback('s', ask['request_id'], 'started', 0, .2))
        self.assertTrue(self.c.feedback('s', ask['request_id'], 'finished', 1, .3))
        self.assertEqual(self.step(.4, 'next')['action'], 'WAIT')
        self.assertEqual(self.step(.9)['action'], 'LISTEN')
        next_ask = self.step(1, 'next')
        self.assertNotEqual(next_ask['request_id'], ask['request_id'])
        self.assertNotEqual(next_ask['question'], ask['question'])

    def test_timeout_never_resends_and_requires_confirmed_recovery(self):
        ask = self.step(0)
        self.c.tick(2)
        self.assertEqual(self.c.phase, 'FAULT')
        self.assertFalse(self.c.feedback('s', ask['request_id'], 'started', 0, 2.1))
        self.assertEqual(self.step(2.2, 'next')['action'], 'WAIT')
        with self.assertRaises(ValueError):
            self.c.recover(2.3, 'unchecked')
        self.c.recover(2.4, 'operator stopped player', playback_stopped=True)
        self.assertEqual(self.step(3)['action'], 'LISTEN')
        next_ask = self.step(3.1, 'next')
        self.assertNotEqual(next_ask['question'], ask['question'])

    def test_duplicates_wrong_session_and_out_of_order_ignored(self):
        ask = self.step(0); rid = ask['request_id']
        self.assertFalse(self.c.feedback('other', rid, 'started', 0, .1))
        self.assertFalse(self.c.feedback('s', rid, 'finished', 9, .2))
        self.assertTrue(self.c.feedback('s', rid, 'started', 0, .3))
        self.assertFalse(self.c.feedback('s', rid, 'started', 0, .4))
        self.assertTrue(self.c.feedback('s', rid, 'finished', 1, .5))
        self.assertFalse(self.c.feedback('s', rid, 'finished', 2, .6))

    def test_finish_timeout_and_legacy_playback_not_ack(self):
        ask = self.step(0)
        self.step(.1, turn='ROBOT')
        self.assertEqual(self.c.phase, 'PROCESSING')
        self.c.feedback('s', ask['request_id'], 'started', 0, .2)
        self.c.tick(3.2)
        self.assertEqual(self.c.phase, 'FAULT')

    def test_stale_id_cannot_finish_new_request(self):
        first = self.step(0)
        self.c.feedback('s', first['request_id'], 'started', 0, .1)
        self.c.feedback('s', first['request_id'], 'finished', 1, .2)
        self.step(1, 'next')
        self.assertFalse(self.c.feedback('s', first['request_id'], 'finished', 2, 1.1))
        self.assertEqual(self.c.phase, 'PROCESSING')

    def test_model_failure_and_invalid_timestamps(self):
        class BadRanker:
            def rank(self, *args):
                return [{'id': 'alien', 'text': 'alien'}]
        self.c.ranker = BadRanker()
        self.assertEqual(self.step(0)['phase'], 'FAULT')
        with self.assertRaises(ValueError):
            self.c.tick(0)
        with self.assertRaises(ValueError):
            self.c.tick(float('nan'))

    def test_empty_pool_and_failed_playback(self):
        saved, self.q = self.q, []
        self.assertEqual(self.step(0)['action'], 'WAIT')
        self.q = saved
        ask = self.step(1)
        self.assertTrue(self.c.feedback('s', ask['request_id'], 'failed', 0, 1.1))
        self.assertEqual(self.c.phase, 'FAULT')
