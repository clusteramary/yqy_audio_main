"""Joint history/question encoding. Import/preflight never starts training."""
import copy
import math
import random
from pathlib import Path

import torch
from torch import nn

from .data import read_jsonl, write_jsonl, validate_ranking, validate_splits
from .followup_data import load_json, save_json, file_sha
from .followup_training import model_fingerprint
from .models.ranker import listwise_loss
from .metrics import average_metrics, ranking_metrics

PROTOCOL = 'cross-encoder-history-v1'


def validate_config(config):
    for name in ('max_length', 'question_tokens', 'batch_size', 'epochs', 'patience'):
        if type(config[name]) is not int or config[name] < 1:
            raise ValueError(name + ' must be a positive integer')
    if config['max_length'] < config['question_tokens'] + 8:
        raise ValueError('reserve space for history and pair special tokens')
    if not math.isfinite(config['learning_rate']) or config['learning_rate'] <= 0:
        raise ValueError('learning_rate must be finite and positive')
    return config


def read_training(config):
    root = Path(config['data'])
    paths = [root / (s + '.jsonl') for s in ('train', 'dev')]
    validate_splits([str(p) for p in paths])
    train, dev = [read_jsonl(p) for p in paths]
    if {r['split'] for r in train} != {'train'} or {r['split'] for r in dev} != {'dev'}:
        raise ValueError('only train and dev may select a model')
    if len({r['dataset'] for r in train + dev}) != 1:
        raise ValueError('dataset protocols differ')
    for r in train + dev:
        if r['dataset'].startswith('KdConv') and r.get('candidate_protocol') != 'same-split-other-dialogue-v2':
            raise ValueError('KdConv candidate protocol must be v2')
    return train, dev, {s: file_sha(p) for s, p in zip(('train', 'dev'), paths)}


def encode_pairs(rows, tokenizer, max_length=512, question_tokens=96):
    """Last eight utterances, history tail; capped question prefix. No K/labels."""
    encoded, sizes = [], []
    specials = tokenizer.num_special_tokens_to_add(pair=True)
    if question_tokens < 1 or max_length - question_tokens - specials < 1:
        raise ValueError('invalid pair budget')
    for row in rows:
        validate_ranking(row, labeled=False)
        history = []
        for text in row['history'][-8:]:
            history.extend(tokenizer.encode(text, add_special_tokens=False))
            history.append(tokenizer.sep_token_id)
        history = history[:-1]
        sizes.append(len(row['candidates']))
        for candidate in row['candidates']:
            question = tokenizer.encode(candidate['text'], add_special_tokens=False)[:question_tokens]
            budget = max_length - specials - len(question)
            encoded.append(tokenizer.prepare_for_model(
                history[-budget:], pair_ids=question, add_special_tokens=True,
                truncation=False, return_attention_mask=True))
    return tokenizer.pad(encoded, padding=True, return_tensors='pt'), sizes


def scores_for(model, tokenizer, rows, config, device):
    inputs, sizes = encode_pairs(rows, tokenizer, config['max_length'], config['question_tokens'])
    flat = model(**{k: v.to(device) for k, v in inputs.items()}).logits.squeeze(-1)
    if flat.ndim != 1 or not torch.isfinite(flat).all():
        raise ValueError('expected finite scalar candidate scores')
    return nn.utils.rnn.pad_sequence(list(flat.split(sizes)), batch_first=True, padding_value=-torch.inf)


def evaluate(model, tokenizer, rows, config, device):
    model.eval()
    values, predictions = [], []
    with torch.inference_mode():
        for start in range(0, len(rows), config['batch_size']):
            batch = rows[start:start + config['batch_size']]
            scores = scores_for(model, tokenizer, batch, config, device).cpu().tolist()
            for row, score in zip(batch, scores):
                validate_ranking(row)
                ranked = sorted(zip(row['candidates'], score), key=lambda x: (-x[1], x[0]['id']))
                values.append(ranking_metrics([c['id'] for c, _ in ranked],
                                             {c['id']: c['relevance'] for c in row['candidates']}))
                predictions.append({'id': row['id'], 'group_id': row['group_id'],
                    'ranked': [{'id': c['id'], 'text': c['text'], 'score': s} for c, s in ranked]})
    return {'count': len(rows), 'metrics': average_metrics(values)}, predictions


def load_local(path):
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(path, num_labels=1, local_files_only=True)
    return model, tokenizer


def preflight(config, forward=False):
    validate_config(config)
    train_rows, dev_rows, hashes = read_training(config)
    report = {'training_started': False, 'protocol': PROTOCOL, 'data_sha256': hashes,
              'train_count': len(train_rows), 'dev_count': len(dev_rows),
              'encoder_sha256': model_fingerprint(config['encoder']),
              'task_warning': 'KdConv labels remain weak; candidate audit must precede formal claims'}
    if forward:
        torch.manual_seed(42)
        model, tokenizer = load_local(config['encoder'])
        model.eval()
        if config['max_length'] > model.config.max_position_embeddings:
            raise ValueError('input exceeds encoder position budget')
        with torch.inference_mode():
            scores = scores_for(model, tokenizer, dev_rows[:1], config, 'cpu')
        report['forward_shape'] = list(scores.shape)
        report['forward_finite'] = bool(torch.isfinite(scores).all())
        report['parameters'] = sum(p.numel() for p in model.parameters())
    return report


def train(config, output, device='cuda', seed=42):
    validate_config(config)
    train_rows, dev_rows, hashes = read_training(config)
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('output must be new or empty')
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    model, tokenizer = load_local(config['encoder'])
    if config['max_length'] > model.config.max_position_embeddings:
        raise ValueError('input exceeds encoder position budget')
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config['learning_rate'], weight_decay=.01)
    rng, history, best, stale = random.Random(seed), [], -1.0, 0
    manifest = {'protocol': PROTOCOL, 'config': copy.deepcopy(config), 'seed': seed,
                'data_sha256': hashes, 'dataset': train_rows[0]['dataset'],
                'encoder_sha256': model_fingerprint(config['encoder']),
                'implementation_sha256': file_sha(Path(__file__)), 'torch': str(torch.__version__),
                'parameters': sum(p.numel() for p in model.parameters()), 'test_used_for_selection': False}
    for epoch in range(config['epochs']):
        model.train()
        order = list(range(len(train_rows)))
        rng.shuffle(order)
        total = 0.0
        for start in range(0, len(order), config['batch_size']):
            rows = [train_rows[i] for i in order[start:start + config['batch_size']]]
            scores = scores_for(model, tokenizer, rows, config, device)
            labels = torch.full(scores.shape, -1, dtype=torch.long, device=device)
            for i, row in enumerate(rows):
                labels[i, :len(row['candidates'])] = torch.tensor([c['relevance'] for c in row['candidates']], device=device)
            optimizer.zero_grad(set_to_none=True)
            loss = listwise_loss(scores, labels)
            if not torch.isfinite(loss):
                raise ValueError('non-finite loss')
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
            optimizer.step()
            total += loss.item() * len(rows)
        metrics, _ = evaluate(model, tokenizer, dev_rows, config, device)
        history.append({'epoch': epoch + 1, 'train_loss': total / len(train_rows), 'dev': metrics})
        print(history[-1], flush=True)
        if metrics['metrics']['MRR'] > best:
            best, stale = metrics['metrics']['MRR'], 0
            model.save_pretrained(output / 'best')
            tokenizer.save_pretrained(output / 'best')
            save_json(output / 'best/run.json', {**manifest, 'best_epoch': epoch + 1,
                                               'checkpoint_sha256': model_fingerprint(output / 'best')})
        else:
            stale += 1
        save_json(output / 'report.json', {**manifest, 'history': history, 'best_dev_MRR': best})
        if stale >= config['patience']:
            break


def evaluate_saved(checkpoint, path, output, device='cpu'):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('evaluation output must be new or empty')
    manifest = load_json(Path(checkpoint) / 'run.json')
    if manifest['protocol'] != PROTOCOL or model_fingerprint(checkpoint) != manifest['checkpoint_sha256']:
        raise ValueError('checkpoint protocol/checksum mismatch')
    rows = read_jsonl(path)
    if {r['dataset'] for r in rows} != {manifest['dataset']} or len({r['split'] for r in rows}) != 1:
        raise ValueError('evaluation dataset/protocol differs')
    split = rows[0]['split']
    if split not in ('dev', 'test'):
        raise ValueError('evaluation requires dev or test')
    if split == 'dev' and file_sha(path) != manifest['data_sha256']['dev']:
        raise ValueError('validation data changed since training')
    model, tokenizer = load_local(checkpoint)
    model.to(device)
    report, predictions = evaluate(model, tokenizer, rows, manifest['config'], device)
    save_json(output / 'evaluation.json', {**report, 'run': manifest, 'split': split, 'input_sha256': file_sha(path)})
    write_jsonl(output / 'predictions.jsonl', predictions)
    return report


class CrossEncoderRuntimeRanker:
    """Controller adapter for a trained, versioned checkpoint; history-only.

    No dataset labels, knowledge strings or offline candidate pool are read.
    An empty/singleton pool needs no neural ranking. Scores are not calibrated
    suitability probabilities and cannot justify a refusal threshold.
    """
    def __init__(self, checkpoint, device='cpu'):
        self.manifest = load_json(Path(checkpoint) / 'run.json')
        if self.manifest['protocol'] != PROTOCOL or model_fingerprint(checkpoint) != self.manifest['checkpoint_sha256']:
            raise ValueError('checkpoint protocol/checksum mismatch')
        self.config, self.device = self.manifest['config'], device
        self.model, self.tokenizer = load_local(checkpoint)
        self.model.to(device).eval()

    def rank(self, history, knowledge, candidates):
        if len(candidates) < 2:
            return [{'id': c['id'], 'text': c['text'], 'score': 0.0} for c in candidates]
        row = {'schema_version': 1, 'id': 'runtime', 'group_id': 'runtime', 'dataset': 'runtime',
               'split': 'inference', 'history': list(history), 'knowledge': [],
               'candidates': [{'id': c['id'], 'text': c['text']} for c in candidates]}
        with torch.inference_mode():
            values = scores_for(self.model, self.tokenizer, [row], self.config, self.device)[0].cpu().tolist()
        result = [{'id': c['id'], 'text': c['text'], 'score': v} for c, v in zip(candidates, values)]
        return sorted(result, key=lambda c: (-c['score'], c['id']))
