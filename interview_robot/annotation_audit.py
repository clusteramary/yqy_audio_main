"""Observable candidate warnings and blinded review packs, not automatic gold labels."""
import copy
import random
import re
from collections import Counter, defaultdict


def warning_flags(history, knowledge, text):
    flags = []
    explicit = bool(re.search(r'[?？]|什么|怎么|为什么|为何|哪[里个些年种]|多少|是否|几[个岁年部]|吗', text))
    if not explicit:
        flags.append('no_explicit_question_cue')
    if text.rstrip('。！! ').endswith('呢') and not explicit:
        flags.append('particle_ne_only')
    if re.search(r'什么都|都是很|你考虑一下', text):
        flags.append('possible_statement_or_suggestion')
    if text.strip() in {h.strip() for h in history}:
        flags.append('exact_history_repeat')
    return flags


def audit(rows):
    counts, examples = Counter(), []
    for row in rows:
        if row['split'] not in ('train', 'dev'):
            raise ValueError('development audit does not inspect test')
        counts['contexts'] += 1
        for item in row['knowledge']:
            subject = item.split('|', 1)[0].strip()
            if subject in {'专辑', '出生', '唱片', '音乐人', '电影', '歌曲'}:
                counts['generic_knowledge_subject_entries'] += 1
        for c in row['candidates']:
            counts['candidate_occurrences'] += 1
            flags = warning_flags(row['history'], row['knowledge'], c['text'])
            counts['flagged_occurrences'] += bool(flags)
            counts.update(flags)
            if flags and len(examples) < 100:
                examples.append({'id': row['id'], 'candidate_id': c['id'], 'text': c['text'], 'flags': flags})
    return {'counts': dict(counts), 'examples': examples,
            'interpretation': 'Heuristic warnings only, overlapping counts; not true error rates or labels. No filtering performed.'}


def review_pack(rows, count=50, seed=2026):
    if count < 1 or any(r['split'] != 'dev' for r in rows):
        raise ValueError('positive count and dev-only rows required')
    groups = defaultdict(lambda: defaultdict(list))
    for r in rows:
        groups[r.get('domain', r['id'].split(':')[0])][r['group_id']].append(r)
    rng, pools = random.Random(seed), {}
    for domain in sorted(groups):
        pool = [rng.choice(sorted(group, key=lambda r: r['id'])) for _, group in sorted(groups[domain].items())]
        rng.shuffle(pool)
        pools[domain] = pool
    selected = []
    while len(selected) < count:
        progress = False
        for domain in sorted(pools):
            if pools[domain] and len(selected) < count:
                selected.append(pools[domain].pop())
                progress = True
        if not progress:
            raise ValueError('not enough distinct dialogues')
    result = []
    for r in selected:
        candidates = [{'id': c['id'], 'text': c['text'],
                       'human_annotation': {'status': 'pending', 'reviewer_id': None, 'relevance': None,
                                            'repeat': None, 'answerable': None, 'depth': None,
                                            'utterance_type': None, 'note': None}} for c in r['candidates']]
        rng.shuffle(candidates)
        result.append({'id': r['id'], 'group_id': r['group_id'], 'history': copy.deepcopy(r['history']),
                       'knowledge': copy.deepcopy(r['knowledge']), 'candidates': candidates})
    return result


def agreement(first, second):
    def flatten(rows):
        items = {}
        for row in rows:
            for c in row['candidates']:
                key = (row['id'], c['id'])
                if key in items:
                    raise ValueError('duplicate annotation identity')
                items[key] = (row, c)
        return items
    a, b = flatten(first), flatten(second)
    if a.keys() != b.keys():
        raise ValueError('annotators must review exactly the same identities')
    pairs, reviewer_a, reviewer_b = [], set(), set()
    fields = {'repeat': (True, False), 'answerable': (True, False), 'depth': (0, 1, 2),
              'utterance_type': ('question', 'mixed', 'statement')}
    extra_pairs = {field: [] for field in fields}
    for key in a:
        ra, ca = a[key]; rb, cb = b[key]
        if ca['text'] != cb['text'] or ra['history'] != rb['history'] or ra['knowledge'] != rb['knowledge']:
            raise ValueError('annotation inputs differ')
        ha, hb = ca['human_annotation'], cb['human_annotation']
        for h in (ha, hb):
            if h['status'] not in ('pending', 'reviewed'):
                raise ValueError('unknown review status')
            rel = h.get('relevance')
            if rel is not None and (type(rel) is not int or rel not in range(4)):
                raise ValueError('invalid relevance')
            for field, allowed in fields.items():
                value = h.get(field)
                if value is not None and (value not in allowed or type(value) is not type(allowed[0])):
                    raise ValueError('invalid annotation: ' + field)
            if h['status'] == 'reviewed' and not (isinstance(h.get('reviewer_id'), str) and h['reviewer_id'].strip()):
                raise ValueError('completed ratings require reviewer IDs')
        if ha['status'] == 'reviewed':
            reviewer_a.add(ha['reviewer_id'])
        if hb['status'] == 'reviewed':
            reviewer_b.add(hb['reviewer_id'])
        if ha['status'] == hb['status'] == 'reviewed':
            for field in fields:
                if ha.get(field) is not None and hb.get(field) is not None:
                    extra_pairs[field].append((ha[field], hb[field]))
        if all(h['status'] == 'reviewed' and h.get('relevance') is not None for h in (ha, hb)):
            if not all(isinstance(h.get('reviewer_id'), str) and h['reviewer_id'].strip() for h in (ha, hb)):
                raise ValueError('completed ratings require reviewer IDs')
            reviewer_a.add(ha['reviewer_id']); reviewer_b.add(hb['reviewer_id'])
            pairs.append((ha['relevance'], hb['relevance']))
    if len(reviewer_a) > 1 or len(reviewer_b) > 1 or reviewer_a & reviewer_b:
        raise ValueError('use two distinct individual annotators')
    result = {'candidate_count': len(a), 'paired_relevance_count': len(pairs),
              'unpaired_or_pending': len(a) - len(pairs), 'quadratic_weighted_kappa': None,
              'scope': 'paired valid values per dimension; file fields cannot certify independent human work'}
    if pairs:
        n = len(pairs)
        left, right = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
        observed = sum((x - y)**2 / 9 for x, y in pairs) / n
        expected = sum(left[i] * right[j] * (i - j)**2 / 9 for i in range(4) for j in range(4)) / n**2
        result.update({'exact_agreement': sum(x == y for x, y in pairs) / n,
                       'within_one_agreement': sum(abs(x-y) <= 1 for x, y in pairs) / n,
                       'marginals': [dict(left), dict(right)],
                       'quadratic_weighted_kappa': 1 - observed / expected if expected > 0 else None})
    result['dimensions'] = {}
    for field, values in extra_pairs.items():
        n = len(values)
        stats = {'paired_count': n, 'unpaired_or_pending': len(a) - n, 'kappa': None,
                 'weighting': 'quadratic' if field == 'depth' else 'unweighted'}
        if n:
            left, right = Counter(x for x, _ in values), Counter(y for _, y in values)
            distance = (lambda x, y: (x-y)**2 / 4) if field == 'depth' else (lambda x, y: float(x != y))
            observed = sum(distance(x, y) for x, y in values) / n
            expected = sum(left[x] * right[y] * distance(x, y) for x in fields[field] for y in fields[field]) / n**2
            stats.update(exact_agreement=sum(x == y for x, y in values) / n,
                         kappa=1-observed/expected if expected > 0 else None,
                         marginals=[dict(left), dict(right)])
        result['dimensions'][field] = stats
    return result
