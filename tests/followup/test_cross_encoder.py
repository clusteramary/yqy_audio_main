import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
from transformers import BertConfig, BertForSequenceClassification, BertTokenizer

from interview_robot import cross_encoder as ce
from interview_robot.data import write_jsonl
from interview_robot.followup_data import save_json, file_sha
from interview_robot.models.ranker import listwise_loss


class CrossEncoderTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.encoder = self.root / 'encoder'
        self.encoder.mkdir()
        (self.encoder / 'vocab.txt').write_text('\n'.join(['[PAD]', '[UNK]', '[CLS]', '[SEP]', '[MASK]', 'old', 'recent', 'question', 'answer', '?']), encoding='utf-8')
        self.tokenizer = BertTokenizer(str(self.encoder / 'vocab.txt'))
        self.model = BertForSequenceClassification(BertConfig(vocab_size=10, hidden_size=16, num_hidden_layers=1,
                                   num_attention_heads=2, intermediate_size=24, max_position_embeddings=64, num_labels=1))
        self.model.save_pretrained(self.encoder)
        self.tokenizer.save_pretrained(self.encoder)
        self.config = dict(data=str(self.root), encoder=str(self.encoder), max_length=32, question_tokens=8,
                           batch_size=2, learning_rate=2e-5, epochs=1, patience=1)
        self.rows = []
        for split, text in [('train', 'old'), ('dev', 'recent')]:
            row = dict(schema_version=1, id=split, group_id=split, dataset='fixture', split=split,
                       history=[text], knowledge=[], candidates=[{'id': 'a', 'text': 'question?', 'relevance': 2},
                                                                {'id': 'b', 'text': 'answer?', 'relevance': 1}])
            self.rows.append(row)
            write_jsonl(self.root / (split + '.jsonl'), [row])

    def tearDown(self):
        self.temp.cleanup()

    def test_tokenization_ignores_gold_knowledge_and_old_history(self):
        row = copy.deepcopy(self.rows[0]); row['history'] = ['old'] + ['recent'] * 8
        before, _ = ce.encode_pairs([row], self.tokenizer, 32, 8)
        row['history'][0] = 'question'; row['knowledge'] = ['answer']; row['knowledge_source'] = 'fixture'
        for c in row['candidates']:
            c['relevance'] = 99; c['source'] = 'gold secret'
        after, _ = ce.encode_pairs([row], self.tokenizer, 32, 8)
        self.assertTrue(all(torch.equal(before[k], after[k]) for k in before))
        self.assertNotIn(self.tokenizer.convert_tokens_to_ids('old'), before['input_ids'])

    def test_padding_multigrade_loss_and_no_weight_update(self):
        extra = copy.deepcopy(self.rows[0])
        extra['candidates'].append({'id': 'c', 'text': 'recent?', 'relevance': 0})
        self.model.eval()
        before = {k: v.clone() for k, v in self.model.state_dict().items()}
        scores = ce.scores_for(self.model, self.tokenizer, [self.rows[0], extra], self.config, 'cpu')
        self.assertEqual(tuple(scores.shape), (2, 3))
        self.assertTrue(torch.isneginf(scores[0, 2]))
        loss = listwise_loss(scores, torch.tensor([[2, 1, -1], [2, 1, 0]]))
        loss.backward()  # gradient-path check only, no optimizer or parameter updates
        self.assertTrue(torch.isfinite(loss))
        self.assertTrue(any(p.grad is not None for p in self.model.parameters()))
        self.assertTrue(all(torch.equal(v, before[k]) for k, v in self.model.state_dict().items()))

    def test_save_load_evaluation_and_checksums_without_training(self):
        manifest = {'protocol': ce.PROTOCOL, 'config': self.config, 'dataset': 'fixture',
                    'data_sha256': {'dev': file_sha(self.root / 'dev.jsonl')},
                    'checkpoint_sha256': ce.model_fingerprint(self.encoder)}
        save_json(self.encoder / 'run.json', manifest)
        self.model.eval()
        direct, _ = ce.evaluate(self.model, self.tokenizer, [self.rows[1]], self.config, 'cpu')
        saved = ce.evaluate_saved(self.encoder, self.root / 'dev.jsonl', self.root / 'evaluation')
        self.assertEqual(direct, saved)
        runtime = ce.CrossEncoderRuntimeRanker(self.encoder)
        original = runtime.rank(['recent'], [], self.rows[1]['candidates'])
        changed = copy.deepcopy(self.rows[1]['candidates'])
        for c in changed:
            c['relevance'] = 99
        self.assertEqual(original, runtime.rank(['recent'], ['future secret'], changed))
        self.assertEqual(runtime.rank(['recent'], [], []), [])
        self.assertEqual(len(runtime.rank(['recent'], [], changed[:1])), 1)
        with self.assertRaises(ValueError):
            ce.evaluate_saved(self.encoder, self.root / 'dev.jsonl', self.root / 'evaluation')
        manifest['checkpoint_sha256'] = 'wrong'; save_json(self.encoder / 'run.json', manifest)
        with self.assertRaises(ValueError):
            ce.evaluate_saved(self.encoder, self.root / 'dev.jsonl', self.root / 'bad')

    def test_preflight_cannot_start_optimizer_or_read_test(self):
        (self.root / 'test.jsonl').write_text('not json', encoding='utf-8')
        with patch('torch.optim.AdamW', side_effect=AssertionError('no training')):
            report = ce.preflight(self.config, forward=True)
        self.assertFalse(report['training_started'])
        self.assertEqual(report['forward_shape'], [1, 2])
        self.assertTrue(report['forward_finite'])

    def test_test_split_cannot_select_checkpoint(self):
        row = copy.deepcopy(self.rows[1]); row['split'] = 'test'
        write_jsonl(self.root / 'dev.jsonl', [row])
        with self.assertRaises(ValueError):
            ce.read_training(self.config)

    def test_budget_retains_question_and_recent_history(self):
        row = copy.deepcopy(self.rows[0]); row['history'] = ['old ' * 100 + 'recent']
        inputs, _ = ce.encode_pairs([row], self.tokenizer, 12, 3)
        self.assertLessEqual(inputs['input_ids'].shape[1], 12)
        self.assertIn(self.tokenizer.convert_tokens_to_ids('recent'), inputs['input_ids'][0])
        self.assertIn(self.tokenizer.convert_tokens_to_ids('question'), inputs['input_ids'][0])
