# -*- coding: utf-8 -*-
# 配置文件：鉴权、WebSocket 地址、麦克风参数等
# 直接按你的提供值填写；如需改设备索引，修改 device_index

import pyaudio

# ===== 鉴权 =====
APP_KEY = "5167059820"
ACCESS_KEY = "spzdT_8qFFeghO2oiFBMaI0p9RbVR35k"

# ===== WebSocket 端点 =====
# 流式识别用 bigmodel（非 *_nostream）
WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"

# ===== 发送封包时长（毫秒） =====
# 建议 200ms/包，既实时又不至于过多包开销
SEGMENT_DURATION_MS = 200

# ===== 目标（上传到 ASR）音频格式 =====
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH_BYTES = 2  # 16bit-LE = 2 bytes

# ===== 麦克风（本地采集）参数：你的设备固定 48k/mono/Int16 =====
input_audio_config = {
    "chunk": 960,                 # 20ms @ 48k（便于再聚合成 200ms 包）
    "format": "pcm",              # 仅注释用途
    "channels": 1,
    "sample_rate": 48000,
    "bit_size": pyaudio.paInt16,  # PyAudio 常量
    "device_index": 10,            # <<< 修改为你的麦克风索引
}

def auth_headers():
    import uuid
    reqid = str(uuid.uuid4())
    return {
        "X-Api-Resource-Id": "volc.bigasr.sauc.duration",
        "X-Api-Request-Id": reqid,
        "X-Api-Access-Key": ACCESS_KEY,
        "X-Api-App-Key": APP_KEY,
    }