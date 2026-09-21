"""Offline controller and a protocol adapter for the retained robot receiver."""
from __future__ import annotations

import json
import socket
import time
from dataclasses import asdict, dataclass
from typing import Optional

from .data import number
from .ranking import LexicalRanker
from .state import InteractionState


@dataclass(frozen=True)
class Decision:
    action: str
    question: Optional[str] = None
    candidate_id: Optional[str] = None
    reason: str = ""

    def to_dict(self):
        return asdict(self)


class InterviewController:
    """Playback acknowledgment and cooldown are required before new speech."""
    def __init__(self, ranker: LexicalRanker, cooldown_seconds: float = 0.5):
        self.ranker = ranker
        self.cooldown = number(cooldown_seconds, "cooldown_seconds")
        self.phase = "LISTEN"
        self._session_id = None
        self._last_timestamp = -1.0
        self._ready_at = 0.0
        self._handled = set()
        self._asked_texts = set()

    def step(self, state: InteractionState, history: list[str], knowledge: list[str],
             candidates: list[dict]) -> Decision:
        if self._session_id is None:
            self._session_id = state.session_id
        if state.session_id != self._session_id:
            raise ValueError("use one controller per session")
        if state.timestamp <= self._last_timestamp:
            raise ValueError("controller timestamps must strictly increase")
        self._last_timestamp = state.timestamp
        if state.turn == "ROBOT":
            self.phase = "SPEAKING"
            return Decision("WAIT", reason="robot playback active")
        if self.phase == "SPEAKING":
            self.phase = "COOLDOWN"
            self._ready_at = state.timestamp + self.cooldown
        if self.phase == "COOLDOWN":
            if state.timestamp < self._ready_at:
                return Decision("WAIT", reason="waiting for residual audio cooldown")
            self.phase = "LISTEN"
        if self.phase == "PROCESSING":
            return Decision("WAIT", reason="awaiting playback acknowledgment")
        if state.turn != "SHIFT":
            return Decision("LISTEN", reason=state.reason)
        if state.utterance_id in self._handled:
            return Decision("LISTEN", reason="utterance already handled")
        remaining = [q for q in candidates if q["text"].strip() not in self._asked_texts]
        if not remaining:
            return Decision("WAIT", reason="no unused candidate; refill candidate pool")
        choice = self.ranker.rank(history, knowledge, remaining)[0]
        self._handled.add(state.utterance_id)
        self._asked_texts.add(choice["text"].strip())
        self.phase = "PROCESSING"
        return Decision("ASK", choice["text"], choice["id"], "highest-ranked unused candidate")

    def cancel_pending(self) -> None:
        """Explicit operator recovery after a failed playback; no automatic retry."""
        self.phase = "LISTEN"


ACTION_INDICES = {"left": 4, "right": 5, "wave": 7, "nod": 8, "shake": 10,
                  "start": 11, "end": 12, "woshou": 13}


class LegacyKeywordAdapter:
    """Default dry run. Socket I/O occurs only when send(..., live=True) is explicit."""
    def __init__(self, host="127.0.0.1", port=5557):
        self.address = (host, port)

    def send(self, keyword: str, live: bool = False) -> dict:
        if keyword not in ACTION_INDICES:
            raise ValueError("unknown legacy action keyword")
        payload = {"type": "voice_keyword", "keyword": keyword, "timestamp": time.time()}
        if live:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
                stream.sendto(json.dumps(payload).encode("utf-8"), self.address)
        return {"payload": payload, "index": ACTION_INDICES[keyword], "sent": live}
