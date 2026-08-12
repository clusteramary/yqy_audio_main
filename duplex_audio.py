"""Wire protocol shared by the ROS speaker publisher and player.

Bare PCM remains valid for half-duplex mode.  Full-duplex mode wraps each PCM
packet with an utterance id so the lower computer can discard interrupted
speech immediately.
"""

import json
import struct
from dataclasses import dataclass
from typing import Optional


_FRAME_MAGIC = b"FDPX"
_FRAME_HEADER_FMT = ">4sIIB"
_FRAME_HEADER_SIZE = struct.calcsize(_FRAME_HEADER_FMT)


@dataclass
class AudioFrame:
    utterance_id: int
    chunk_seq: int
    is_last: bool
    payload: bytes


@dataclass
class ControlMessage:
    cmd: str
    utterance_id: int
    reason: str
    timestamp: float = 0.0


def pack_audio_frame(
    utterance_id: int,
    chunk_seq: int,
    is_last: bool,
    payload: bytes,
) -> bytes:
    header = struct.pack(
        _FRAME_HEADER_FMT,
        _FRAME_MAGIC,
        utterance_id,
        chunk_seq,
        1 if is_last else 0,
    )
    return header + struct.pack(">I", len(payload)) + payload


def try_unpack_audio_frame(data: bytes) -> Optional[AudioFrame]:
    if len(data) < _FRAME_HEADER_SIZE + 4 or data[:4] != _FRAME_MAGIC:
        return None
    try:
        _magic, utterance_id, chunk_seq, is_last_byte = struct.unpack_from(
            _FRAME_HEADER_FMT, data
        )
        payload_len = struct.unpack_from(">I", data, _FRAME_HEADER_SIZE)[0]
        payload_start = _FRAME_HEADER_SIZE + 4
        if len(data) < payload_start + payload_len:
            return None
        return AudioFrame(
            utterance_id=utterance_id,
            chunk_seq=chunk_seq,
            is_last=bool(is_last_byte),
            payload=data[payload_start : payload_start + payload_len],
        )
    except Exception:
        return None


def build_stop_message(utterance_id: int, reason: str = "barge_in") -> str:
    import time

    return json.dumps(
        {
            "cmd": "stop",
            "utterance_id": utterance_id,
            "reason": reason,
            "timestamp": time.time(),
        },
        ensure_ascii=False,
    )


def parse_control_message(data: str) -> Optional[ControlMessage]:
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    cmd = obj.get("cmd")
    utterance_id = obj.get("utterance_id")
    if not cmd or utterance_id is None:
        return None
    return ControlMessage(
        cmd=str(cmd),
        utterance_id=int(utterance_id),
        reason=str(obj.get("reason", "")),
        timestamp=float(obj.get("timestamp", 0.0)),
    )
