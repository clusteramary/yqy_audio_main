"""
Full-duplex audio utilities: frame packing/unpacking, control messages, barge-in detection.
"""

import math
import struct
import time
from typing import Optional, Tuple

# ---------- Audio frame helpers (ROS /audio transport) ----------

# Frame header: magic(2B) + utterance_id_len(2B) + chunk_seq(4B) + payload_len(4B) + is_last(1B)
_FRAME_MAGIC = b"\xDA\xF1"
_FRAME_HDR_SIZE = 2 + 2 + 4 + 4 + 1  # 13 bytes


def pack_audio_frame(
    pcm_bytes: bytes,
    utterance_id: str = "",
    chunk_seq: int = 0,
    is_last: bool = False,
) -> bytes:
    """Pack PCM bytes into a framed audio message with metadata.

    If utterance_id is empty, returns raw pcm_bytes (legacy compatibility).
    """
    if not utterance_id:
        return pcm_bytes
    uid_bytes = utterance_id.encode("utf-8")
    header = _FRAME_MAGIC
    header += struct.pack(">H", len(uid_bytes))
    header += struct.pack(">I", chunk_seq)
    header += struct.pack(">I", len(pcm_bytes))
    header += struct.pack("B", 1 if is_last else 0)
    return header + uid_bytes + pcm_bytes


def try_unpack_audio_frame(
    data: bytes,
) -> Optional[Tuple[str, int, bool, bytes]]:
    """Try to unpack a framed audio message.

    Returns (utterance_id, chunk_seq, is_last, pcm_bytes) or None if legacy raw PCM.
    """
    if len(data) < _FRAME_HDR_SIZE:
        return None
    magic = data[:2]
    if magic != _FRAME_MAGIC:
        return None
    try:
        offset = 2
        uid_len = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
        chunk_seq = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
        payload_len = struct.unpack(">I", data[offset:offset + 4])[0]
        offset += 4
        is_last = struct.unpack("B", data[offset:offset + 1])[0] != 0
        offset += 1
        uid = data[offset : offset + uid_len].decode("utf-8")
        offset += uid_len
        pcm = data[offset : offset + payload_len]
        return uid, chunk_seq, is_last, pcm
    except Exception:
        return None


# ---------- Control message helpers (/audio/control topic) ----------

def build_stop_message(utterance_id: str = "", reason: str = "barge_in") -> dict:
    """Build a stop control message for the /audio/control topic."""
    return {
        "command": "stop",
        "utterance_id": utterance_id,
        "reason": reason,
        "timestamp": time.time(),
    }


def parse_control_message(msg_dict: dict) -> Optional[Tuple[str, str, str]]:
    """Parse a control message. Returns (command, utterance_id, reason) or None."""
    cmd = msg_dict.get("command", "")
    if cmd == "stop":
        return cmd, msg_dict.get("utterance_id", ""), msg_dict.get("reason", "")
    return None


# ---------- Barge-in detection (energy-based VAD) ----------

class BargeInDetector:
    """Simple energy-based barge-in detector.

    Triggers when RMS energy exceeds threshold for a minimum consecutive duration.
    """

    def __init__(
        self,
        threshold: float = 500.0,
        min_duration_ms: int = 300,
        sample_rate: int = 16000,
        sample_width: int = 2,
    ):
        self.threshold = threshold
        self.min_duration_ms = min_duration_ms
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self._above_since: Optional[float] = None

    def reset(self):
        self._above_since = None

    def feed(self, pcm_chunk: bytes) -> bool:
        """Feed a PCM chunk and return True if barge-in is detected."""
        if len(pcm_chunk) < self.sample_width:
            return False
        rms = _compute_rms(pcm_chunk, self.sample_width)
        now = time.time()

        if rms >= self.threshold:
            if self._above_since is None:
                self._above_since = now
            elapsed_ms = (now - self._above_since) * 1000.0
            if elapsed_ms >= self.min_duration_ms:
                self.reset()
                return True
        else:
            self._above_since = None
        return False


def _compute_rms(pcm_bytes: bytes, sample_width: int = 2) -> float:
    """Compute RMS energy of a PCM buffer (little-endian signed integer)."""
    n_samples = len(pcm_bytes) // sample_width
    if n_samples == 0:
        return 0.0
    if sample_width == 2:
        fmt = "<{}h".format(n_samples)
    elif sample_width == 4:
        fmt = "<{}i".format(n_samples)
    else:
        return 0.0
    try:
        samples = struct.unpack(fmt, pcm_bytes[: n_samples * sample_width])
    except struct.error:
        return 0.0
    sum_sq = sum(s * s for s in samples)
    return math.sqrt(sum_sq / n_samples)
