import copy
import unittest
from interview_robot.annotation_audit import warning_flags, review_pack, agreement, audit


class AuditTests(unittest.TestCase):
    def rows(self):
        return [dict(id=f'{d}:{i}', group_id=f'{d}:{i}', domain=d, split='dev', history=['past'], knowledge=[],
                     candidates=[{'id': 'a', 'text': 'question?', 'relevance': 1, 'source': 'gold'},
                                 {'id': 'b', 'text': 'answer', 'relevance': 0}]) for d in ('a', 'b') for i in range(5)]

    def test_blind_pack_preserves_source_and_hides_labels(self):
        rows = self.rows(); before = copy.deepcopy(rows)
        pack = review_pack(rows, 6)
        self.assertEqual(rows, before)
        self.assertEqual(pack, review_pack(list(reversed(rows)), 6))
        self.assertEqual(len({r['group_id'] for r in pack}), 6)
        self.assertTrue(all('relevance' not in c and 'source' not in c for r in pack for c in r['candidates']))
        self.assertEqual(agreement(pack, pack)['paired_relevance_count'], 0)

    def test_weighted_agreement_and_same_reviewer_rejected(self):
        a = review_pack(self.rows(), 2); b = copy.deepcopy(a)
        for rows, name in [(a, 'A'), (b, 'B')]:
            for r in rows:
                for i, c in enumerate(r['candidates']):
                    c['human_annotation'].update(status='reviewed', reviewer_id=name, relevance=i)
        self.assertEqual(agreement(a, b)['quadratic_weighted_kappa'], 1)
        with self.assertRaises(ValueError):
            agreement(a, a)
        b[0]['candidates'][0]['text'] = 'changed'
        with self.assertRaises(ValueError):
            agreement(a, b)

    def test_test_sealed_and_rules_are_only_warnings(self):
        self.assertIn('particle_ne_only', warning_flags([], [], '都是很不错的游玩圣地呢。'))
        rows = self.rows(); rows[0]['split'] = 'test'
        with self.assertRaises(ValueError):
            audit(rows)
        with self.assertRaises(ValueError):
            review_pack(rows, 2)

    def test_multidimensional_missing_and_degenerate_agreement(self):
        a = review_pack(self.rows(), 2); b = copy.deepcopy(a)
        for rows, name in [(a, 'A'), (b, 'B')]:
            for r in rows:
                for i, c in enumerate(r['candidates']):
                    c['human_annotation'].update(status='reviewed', reviewer_id=name, relevance=i,
                        repeat=bool(i), answerable=True, depth=i, utterance_type='question')
        report = agreement(a, b)
        self.assertEqual(report['dimensions']['repeat']['kappa'], 1)
        self.assertEqual(report['dimensions']['depth']['kappa'], 1)
        self.assertIsNone(report['dimensions']['answerable']['kappa'])
        b[0]['candidates'][0]['human_annotation']['repeat'] = None
        self.assertEqual(agreement(a, b)['dimensions']['repeat']['paired_count'], 3)
        b[0]['candidates'][0]['human_annotation']['answerable'] = 1
        with self.assertRaises(ValueError):
            agreement(a, b)
