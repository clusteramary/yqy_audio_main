from dataclasses import dataclass
from typing import Dict, List, Optional

# 采样相关常量
TARGET_SAMPLE_RATE = 16000
TARGET_SAMPLE_WIDTH = 2
TARGET_CHANNELS = 1
TARGET_CHUNK_SAMPLES = 320  # 16k * 20ms = 320 样本 → 每帧约 20ms

# ASR 关键词配置：标签 -> 若干“包含匹配”的短语（用于语音识别结果）
ASR_KWS_PATTERNS: Dict[str, List[str]] = {
    "wave": ["挥手", "挥一挥", "挥一下", "wave"],
    "nod": ["点头", "点一下", "nod"],
    "shake": ["击掌", "击一下"],
    "woshou": ["握手", "握一下", "握个手", "shake"],
    "end": ["再见", "拜拜", "bye"],
}

# LLM 文本关键词配置：标签 -> 若干“包含匹配”的短语（用于大模型文本 content）
LLM_KWS_PATTERNS: Dict[str, List[str]] = {
    "left": ["向左转", "向左"],
    "right": ["向右", "测试成功啦"],
    # 结束访谈/结束控制，由 LLM 说出
    "end": ["感谢你", "感谢您", "感谢"],
    # 后面可以继续加，比如:
    # "stop": ["停一下", "停止", "先到这里"],
}


@dataclass
class AudioConfig:
    format: str
    bit_size: int
    channels: int
    sample_rate: int
    chunk: int
    device_index: Optional[int] = None
    mode: str = "pyaudio"
    ros1_topic: str = "/robot/speaker/audio"
    ros1_node_name: str = "speaker_publisher"
    ros1_queue_size: int = 10
    ros1_latch: bool = False
