"""Causal rule baseline, explicitly not a trained multimodal estimator."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from .data import number, validate_observation


@dataclass(frozen=True)
class InteractionState:
    session_id: str
    utterance_id: str
    timestamp: float
    speaker_id: Optional[str]
    turn: str
    affect: str
    available_modalities: tuple[str, ...]
    evidence_confidence: dict[str, float]
    reason: str
    model: str = "causal-rule-v1"

    def to_dict(self) -> dict:
        return asdict(self)


class RuleStateEstimator:
    def __init__(self, silence_seconds: float = 0.8, max_age_seconds: float = 2.0,
                 min_confidence: float = 0.3):
        self.silence_seconds = number(silence_seconds, "silence_seconds")
        self.max_age_seconds = number(max_age_seconds, "max_age_seconds")
        self.min_confidence = number(min_confidence, "min_confidence", upper=1)
        self._sessions: dict[str, dict] = {}

    def predict(self, observation: dict) -> InteractionState:
        validate_observation(observation)
        sid, now = observation["session_id"], observation["timestamp"]
        memory = self._sessions.setdefault(sid, {"timestamp": -1, "last_speech": None, "utterance": None})
        if now <= memory["timestamp"]:
            raise ValueError("timestamps must strictly increase within each session")
        if observation["utterance_id"] != memory["utterance"]:
            memory["last_speech"] = None
            memory["utterance"] = observation["utterance_id"]
        memory["timestamp"] = now
        evidence = {
            name: item for name, item in observation["modalities"].items()
            if now - item["timestamp"] <= self.max_age_seconds
            and item["confidence"] >= self.min_confidence
        }
        audio, text, visual = (evidence.get(name, {}) for name in ("audio", "text", "visual"))
        if observation["robot_playing"]:
            turn, reason = "ROBOT", "playback feedback is active"
        elif audio.get("speaking"):
            memory["last_speech"] = now
            turn, reason = "HOLD", "current user speech activity"
        elif text.get("text") and not text.get("final"):
            turn, reason = "HOLD", "ASR utterance is still partial"
        elif audio and not audio["speaking"] and text.get("text") and text.get("final"):
            # Missing audio does not imply silence. ASR final alone is not a turn boundary.
            last = memory["last_speech"]
            if last is not None and now - last >= self.silence_seconds:
                turn, reason = "SHIFT", "final transcript and observed silence after speech"
            else:
                turn, reason = "HOLD", "waiting for enough observed silence"
        else:
            turn, reason = "IDLE", "insufficient current evidence for a turn decision"
        return InteractionState(
            sid, observation["utterance_id"], now, visual.get("speaker_id"), turn,
            visual.get("affect", "unknown"), tuple(sorted(evidence)),
            {key: value["confidence"] for key, value in evidence.items()}, reason,
        )
