"""Request-correlated, fail-closed offline controller. No socket or audio I/O."""
import math
import uuid

from .data import number, nonempty


class ConfirmedInterviewController:
    def __init__(self, session_id, ranker, start_timeout=5.0, finish_timeout=60.0, cooldown=.5):
        self.session_id = nonempty(session_id, 'session_id')
        self.ranker = ranker
        self.start_timeout = number(start_timeout, 'start_timeout', lower=.001)
        self.finish_timeout = number(finish_timeout, 'finish_timeout', lower=.001)
        self.cooldown = number(cooldown, 'cooldown')
        self.phase, self.pending = 'LISTEN', None
        self.last_time, self.deadline, self.ready_at = -1.0, None, 0.0
        self.handled, self.asked, self.events = set(), set(), []

    def log(self, now, event, **fields):
        self.events.append({'schema_version': 1, 'session_id': self.session_id, 'timestamp': now,
                            'event': event, 'phase': self.phase,
                            'request_id': self.pending['request_id'] if self.pending else None, **fields})

    def advance(self, now):
        now = number(now, 'timestamp')
        if now <= self.last_time:
            raise ValueError('local receipt timestamps must strictly increase')
        self.last_time = now
        if self.phase in ('PROCESSING', 'SPEAKING') and now >= self.deadline:
            reason = 'start_timeout' if self.phase == 'PROCESSING' else 'finish_timeout'
            self.phase = 'FAULT'
            self.log(now, reason)
        if self.phase == 'COOLDOWN' and now >= self.ready_at:
            self.phase, self.pending = 'LISTEN', None
            self.log(now, 'cooldown_complete')
        return now

    def tick(self, now):
        """Caller must schedule ticks even when no new observations arrive."""
        self.advance(now)
        return self.phase

    def step(self, state, history, knowledge, candidates):
        if state.session_id != self.session_id:
            raise ValueError('wrong session')
        now = self.advance(state.timestamp)
        result = {'action': 'WAIT', 'phase': self.phase, 'request_id': None}
        if self.phase != 'LISTEN' or state.turn == 'ROBOT':
            return {**result, 'reason': 'execution locked or playback active'}
        if state.turn != 'SHIFT' or state.utterance_id in self.handled:
            return {**result, 'action': 'LISTEN', 'reason': 'no new allowed turn'}
        remaining = []
        ids = set()
        for c in candidates:
            cid, text = nonempty(c.get('id'), 'candidate.id'), nonempty(c.get('text'), 'candidate.text')
            if cid in ids:
                raise ValueError('duplicate candidate id')
            ids.add(cid)
            if text.strip() not in self.asked:
                remaining.append({'id': cid, 'text': text})
        if not remaining:
            self.log(now, 'candidate_pool_empty')
            return {**result, 'reason': 'no unused candidates'}
        try:
            ranked = self.ranker.rank(history, knowledge, remaining)
            choice = ranked[0]
            expected = {c['id']: c['text'] for c in remaining}
            if choice['id'] not in expected or choice['text'] != expected[choice['id']]:
                raise ValueError('ranker returned foreign candidate')
            if not math.isfinite(choice.get('score', 0)):
                raise ValueError('non-finite ranking score')
        except Exception as error:
            self.phase = 'FAULT'
            self.log(now, 'ranking_failed', error_type=type(error).__name__)
            return {**result, 'phase': self.phase, 'reason': 'ranking failed; operator recovery required'}
        self.pending = {'request_id': str(uuid.uuid4()), 'utterance_id': state.utterance_id,
                        'candidate_id': choice['id'], 'question': choice['text'], 'last_sequence': -1}
        self.handled.add(state.utterance_id)
        self.asked.add(choice['text'].strip())
        self.phase, self.deadline = 'PROCESSING', now + self.start_timeout
        self.log(now, 'speech_requested', utterance_id=state.utterance_id,
                 candidate_id=choice['id'], question=choice['text'])
        return {'action': 'ASK', 'phase': self.phase, **self.pending}

    def feedback(self, session_id, request_id, kind, sequence, now):
        if kind not in ('started', 'finished', 'failed') or type(sequence) is not int or sequence < 0:
            raise ValueError('invalid playback event')
        now = self.advance(now)
        if session_id != self.session_id or not self.pending or request_id != self.pending['request_id']:
            self.log(now, 'feedback_ignored', reason='foreign_or_expired_request', received_request_id=request_id)
            return False
        if sequence <= self.pending['last_sequence']:
            self.log(now, 'feedback_ignored', reason='duplicate_or_old_sequence')
            return False
        allowed = (kind == 'started' and self.phase == 'PROCESSING') or (
            kind == 'finished' and self.phase == 'SPEAKING') or (
            kind == 'failed' and self.phase in ('PROCESSING', 'SPEAKING'))
        if not allowed:
            self.log(now, 'feedback_ignored', reason='invalid_phase', received_kind=kind)
            return False
        self.pending['last_sequence'] = sequence
        if kind == 'started':
            self.phase, self.deadline = 'SPEAKING', now + self.finish_timeout
        elif kind == 'finished':
            self.phase, self.ready_at = 'COOLDOWN', now + self.cooldown
        else:
            self.phase = 'FAULT'
        self.log(now, 'playback_' + kind)
        return True

    def recover(self, now, reason, playback_stopped=False):
        nonempty(reason, 'operator recovery reason')
        if self.phase != 'FAULT' or playback_stopped is not True:
            raise ValueError('recovery requires FAULT and explicit stopped-playback confirmation')
        now = self.advance(now)
        self.phase, self.ready_at = 'COOLDOWN', now + self.cooldown
        self.log(now, 'operator_recovery', reason=reason)
        # Asked/handled sets deliberately survive. Never automatically replay a request.
