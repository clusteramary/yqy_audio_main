import uuid

import pyaudio

ws_connect_config = {
    "base_url": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
    "headers": {
        "X-Api-App-ID": "5167059820",
        "X-Api-Access-Key": "spzdT_8qFFeghO2oiFBMaI0p9RbVR35k",
        "X-Api-Resource-Id": "volc.speech.dialog",
        "X-Api-App-Key": "PlgvMymc7f3tQnJ6",
        "X-Api-Connect-Id": str(uuid.uuid4()),
    },
}

start_session_req = {
    "tts": {
        "speaker": "zh_female_vv_jupiter_bigtts",
        "audio_config": {"channel": 1, "format": "pcm", "sample_rate": 24000},
    },
    "dialog": {
        "bot_name": "华科机器人",
        "system_role": "你使用活泼灵动的女声，性格开朗，热爱生活。",
        "speaking_style": "你的说话风格简洁明了，语速适中，语调自然。",
        "location": {"city": "北京"},
        "extra": {
            "strict_audit": False,
            "audit_response": "当我用开心的语气说话，你就用开心的语气说话，当我用悲伤的语气说话，你就用悲伤的语气说话。",
        },
    },
}

# 输入音频（麦克风）— 只能 48k，所以这里按 48k 打开
input_audio_config = {
    "chunk": 960,  # 20ms @ 48k（重采样后更易切成 10ms@16k 的整帧）
    "format": "pcm",
    "channels": 1,
    "sample_rate": 48000,  # ← 只能 48k 的设备
    "bit_size": pyaudio.paInt16,
    "device_index": 2,  # ← 你的麦克风索引
}

# 输出音频（扬声器）
output_audio_config = {
    "chunk": 3200,
    "format": "pcm",
    "channels": 1,
    "sample_rate": 24000,
    "bit_size": pyaudio.paFloat32,
    "device_index": None,
}
