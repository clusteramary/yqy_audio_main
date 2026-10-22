"""Export manually authored AI judgments; does not infer labels or change training data."""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/processed/followup-v2-mixed/kdconv/human_audit.jsonl'
DEST = ROOT / 'data/annotations'


def main():
    original = SOURCE.read_bytes()
    source_sha = hashlib.sha256(original).hexdigest()
    rows = {r['id']: r for r in map(json.loads, original.decode('utf-8').splitlines())}
    authored = json.loads((DEST / 'kdconv_pilot_judgments.json').read_text(encoding='utf-8'))
    records = []
    counts = Counter()
    md = [
        '# 创新点二：AI初标示例与复核', '',
        '已完成 6 段验证集对话、60 条候选的第一批 AI 初标。影视、音乐、旅行各 2 段不同对话；按盲审文件现有顺序取每领域前两个不同对话，属于教学试标，不代表总体比例。', '',
        '## 你怎么标', '',
        '1. 先读完整历史 H，确定当前人物/景点/作品，以及哪些信息已经回答。',
        '2. 再看知识 K，先确认是不是同一个实体。知识里出现答案，不代表历史已经回答；没有知识，也不等于不能回答。',
        '3. 逐条判断相关性：0=脱题、指代不明或明确冲突；1=弱关联或冗余；2=合理延续；3=紧扣当前内容、有针对性推进。同一组可以有多个好问题，也可以没有。',
        '4. 判断重复、可回答性和深度；不确定填 null 并写原因。重复指已获实质回答，无必要澄清目的；未获答的机械重问可降低相关性，但不因此标已答重复。',
        '5. 看是否真的在提问。statement 是陈述，question 是问题，mixed 是陈述加问题。陈述可以高度相关，但不属于有效追问。建议性反问也需复核。', '',
        '相关性评分评价整句在当前对话中的适用性，重复会降低评分。answerable 评价整句是否可直接回应：明确错误前提填 false；可纠错不等于原句可接受；真假不确定填 null。纯陈述的 answerable/depth 填 null。depth=0 基础事实/确认，1 解释或比较，2 原因/影响/经历/观点的进一步探究；本批没有可靠的深层追问，不强行凑标签。', '',
        '## 怎么使用这些文件', '',
        '- `data/annotations/kdconv_ai_pilot.jsonl`：完整上下文、候选、AI判断及理由。`ai_annotation` 与 `human_annotation` 分开；真人字段全部留空。',
        '- 你可以复制 JSONL 为自己的复核版本，填 `human_annotation`，将 status 改成 reviewed，填写 reviewer_id 和 note；不要直接把所有 AI 标签复制成真人标签。',
        '- 受 AI 提示后复核属于辅助标注。论文若报告两人独立一致性，两人必须另用不含 AI 判断的原始 `human_audit.jsonl` 独立标注，不能把 AI 和你算成两名独立真人。',
        '- `kdconv_pilot_judgments.json` 是这批人工编写的 AI 判断源文件，每项依次为 relevance、repeat、answerable、depth、utterance_type、needs_review、note；导出脚本只整理格式，不自动判断。',
        '- 重建命令：在仓库根目录执行 `.venv-meld\\Scripts\\python.exe scripts\\export_annotation_pilot.py`。', '',
        '## 这批暴露的问题', '',
        '古陶文明博物馆那组中，“周边有前门大街……”很好地回答了上一句，却没有追问。这提示当前候选里混入普通回复，需要先定义任务是下一轮回复排序还是追问排序，再审计整个候选构造流程；本次没有直接过滤或重建训练集。', '',
        '陈奕迅专辑那组的 K 包含其他专辑，不能因 K8 写“中国大陆”就认定当前专辑的地区。娃娃那组存在艺名同名实体混淆风险；暂按历史明确提及的金智娟进行局部判断，但整段事实身份需进一步核实。知识冲突仅针对提供的语料快照，不是对现实票价、地址或人物资料的实时核验。', '',
        '本批只读取已去除来源和自动标签的盲审文件进行判断，没有用原始下一句位置挑答案。AI 初标不是金标准，不能据此报告模型效果、真人一致性或全量弱标签错误率。训练集、test 集和原始弱标签均未改动；本批没有接入训练。', '',
        '下一步：先复核本批疑点并固定量表，检查候选是否含有效问题以及知识实体对齐，再扩展到手册建议的 30–50 段 dev 双人试标。确认多正例评价协议后，才决定训练集如何重标或过滤。', '',
        f'盲审源文件 SHA256：`{source_sha}`。', '',
        '## 逐条初标', '',
        '下表“复核”表示 AI 判断有具体疑点；全部记录仍需要真人审核。是/否/待定分别对应 true/false/null。', '',
    ]
    fmt = lambda v: '待定' if v is None else ('是' if v is True else ('否' if v is False else str(v)))
    for rid, judgments in authored.items():
        row = rows[rid]
        assert ':dev:' in rid and len(judgments) == len(row['candidates']) == 10
        row['annotation_metadata'] = {
            'kind': 'ai_preliminary', 'author': 'Codex AI assistant',
            'date': '2026-09-29', 'rubric': 'pilot-v1.1',
            'source': SOURCE.relative_to(ROOT).as_posix(), 'source_sha256': source_sha,
            'weak_labels_seen': False, 'human_review_complete': False,
            'sampling': 'first_two_distinct_dialogues_per_domain_in_blinded_file',
        }
        md += [f'### {rid}', '', '**历史**', '']
        md += [f'- H{i}: {s}' for i, s in enumerate(row['history'], 1)]
        md += ['', '**知识快照（可能含检索错误）**', '']
        md += [f'- K{i}: {s}' for i, s in enumerate(row['knowledge'], 1)]
        md += ['', '|编号/候选|相关性|重复|可回答|深度|类型|复核|理由|', '|---|---|---|---|---|---|---|---|']
        for i, (c, j) in enumerate(zip(row['candidates'], judgments), 1):
            relevance, repeat, answerable, depth, kind, review, note = j
            assert type(relevance) is int and relevance in range(4)
            assert type(repeat) is bool and (answerable is None or type(answerable) is bool)
            assert depth is None or (type(depth) is int and depth in range(3))
            assert kind in ('question', 'statement', 'mixed') and type(review) is bool and note
            assert all(c.get(k) is None for k in ('human_relevance', 'repeat', 'answerable'))
            c['ai_annotation'] = dict(zip(
                ['relevance', 'repeat', 'answerable', 'depth', 'utterance_type', 'needs_review', 'note'], j))
            c['human_annotation'] = {
                'status': 'pending', 'reviewer_id': None, 'relevance': None,
                'repeat': None, 'answerable': None, 'depth': None, 'utterance_type': None, 'note': None,
            }
            counts[f'relevance_{relevance}'] += 1
            counts['flagged_for_review'] += review
            counts['statement'] += kind == 'statement'
            counts['repeat'] += repeat
            counts['answerable_unknown'] += answerable is None
            cells = [f'C{i} {c["text"]}', relevance, repeat, answerable, depth, kind, review, note]
            md.append('| ' + ' | '.join(fmt(x).replace('|', '\\|').replace('\n', ' ') for x in cells) + ' |')
        md.append('')
        records.append(row)
    assert len(records) == 6 and sum(len(r['candidates']) for r in records) == 60
    assert len({r['id'].rsplit(':', 1)[0] for r in records}) == 6
    assert SOURCE.read_bytes() == original
    counts['contexts'] = len(records)
    counts['candidates'] = 60
    (DEST / 'kdconv_ai_pilot.jsonl').write_text(
        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
    (DEST / 'kdconv_ai_pilot_summary.json').write_text(
        json.dumps({'scope': 'AI preliminary pilot only; not gold labels', 'counts': dict(counts),
                    'source_sha256': source_sha}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'docs/创新点二_AI初标示例与复核.md').write_text('\n'.join(md), encoding='utf-8')
    print(json.dumps(dict(counts), ensure_ascii=True))


if __name__ == '__main__':
    main()
