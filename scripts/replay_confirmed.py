"""Deterministic scenario inputs with synthetic acknowledgments; no real device."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interview_robot.confirmed_runtime import ConfirmedInterviewController
from interview_robot.ranking import LexicalRanker
from interview_robot.state import InteractionState
from interview_robot.data import write_jsonl
from interview_robot.followup_data import save_json


def replay(ranker=None):
    def state(t, u):
        return InteractionState('replay', u, t, None, 'SHIFT', 'unknown', (), {}, 'synthetic')
    c = ConfirmedInterviewController('replay', ranker or LexicalRanker(), start_timeout=2, finish_timeout=3)
    questions = [{'id': 'q1', 'text': 'career growth?'}, {'id': 'q2', 'text': 'career learning?'},
                 {'id': 'q3', 'text': 'career goals?'}]
    first = c.step(state(0, 'u1'), ['career'], [], questions)
    c.feedback('replay', first['request_id'], 'finished', 2, .1)  # out of order
    c.feedback('replay', first['request_id'], 'started', 0, .2)
    c.feedback('replay', first['request_id'], 'started', 0, .3)  # duplicate
    c.feedback('replay', first['request_id'], 'finished', 1, .4)
    c.tick(1)
    second = c.step(state(1.1, 'u2'), ['career'], [], questions)
    c.feedback('replay', first['request_id'], 'finished', 3, 1.2)  # expired ID
    c.tick(3.2)  # lost start acknowledgement
    assert c.phase == 'FAULT'
    assert c.step(state(3.3, 'u3'), ['career'], [], questions)['action'] == 'WAIT'
    c.recover(3.4, 'simulated operator verified playback stopped', playback_stopped=True)
    c.tick(4)
    third = c.step(state(4.1, 'u3'), ['career'], [], questions)
    c.feedback('replay', third['request_id'], 'started', 0, 4.2)
    c.tick(7.3)  # lost finish acknowledgement
    assert c.phase == 'FAULT'
    return {'simulation_only': True, 'hardware_connected': False, 'training_started': False,
            'final_phase': c.phase, 'requests': 3, 'events': len(c.events)}, c.events


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default='outputs/confirmed-replay')
    p.add_argument('--checkpoint', help='optional trained Cross-Encoder directory; default uses BM25')
    a = p.parse_args()
    root = Path(a.output)
    if root.exists() and any(root.iterdir()):
        p.error('output must be new or empty')
    ranker = None
    if a.checkpoint:
        from interview_robot.cross_encoder import CrossEncoderRuntimeRanker
        ranker = CrossEncoderRuntimeRanker(a.checkpoint)
    report, events = replay(ranker)
    report['ranker'] = 'trained-cross-encoder' if ranker else 'BM25'
    write_jsonl(root / 'events.jsonl', events)
    save_json(root / 'report.json', report)
    print(json.dumps(report))
