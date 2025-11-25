# config.py
import uuid

import pyaudio

runtime_control_path = "./yuying/a.txt"


# ============ WebSocket 连接配置 ============
# ws_connect_config = {
#     "base_url": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
#     "headers": {
#         "X-Api-App-ID": "4601805855",
#         "X-Api-Access-Key": "6b4WXX4EfPhh8W2oLF2B-A9h69BP-qyj",
#         "X-Api-Resource-Id": "volc.speech.dialog",
#         "X-Api-App-Key": "PlgvMymc7f3tQnJ6",
#         "X-Api-Connect-Id": str(uuid.uuid4()),
#     },
# }

ws_connect_config = {
    "base_url": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
    "headers": {
        "X-Api-App-ID": "7381194560",
        "X-Api-Access-Key": "PmMJqNvQDStP4xpTi4pnuO83F793BplS",
        "X-Api-Resource-Id": "volc.speech.dialog",
        "X-Api-App-Key": "PlgvMymc7f3tQnJ6",
        "X-Api-Connect-Id": str(uuid.uuid4()),
    },
}


# ============ 会话启动参数（下行 TTS 配置） ============
# 说明：这里声明了服务端 TTS 的输出格式，当前为 24k / pcm / 单声道
start_session_req = {
    "tts": {
        "speaker": "zh_female_vv_jupiter_bigtts",
        "audio_config": {
            "channel": 1,
            "format": "pcm",
            "sample_rate": 24000,
        },
    },
    "dialog": {
        "bot_name": "华科机器人",
        "system_role": "你使用活泼灵动的女声，性格开朗，热爱生活。",
        "speaking_style": "你的说话风格简洁明了，语速适中，语调自然。",
        "location": {"city": "武汉"},
        "extra": {
            "strict_audit": False,
            # "strict_audit": True,
            "audit_response": "当我用开心的语气说话，你就用开心的语气说话，当我用悲伤的语气说话，你就用悲伤的语气说话。",
            # "audit_response": "文化宫先往左走再往前走，少年宫先往右边走再往左边走。",
        },
    },
}

# ============ 输入音频（麦克风，直连） ============
# # 你的设备只能 48k，这里按 48k 打开；上层会在发送前重采样到 16k
# input_audio_config = {
#     "chunk": 960,  # 20ms @ 48k（便于下游再切 10ms@16k 整帧）
#     "format": "pcm",
#     "channels": 1,
#     "sample_rate": 48000,  # 你的麦克风固定 48k
#     # "sample_rate": 44100,  # 你的麦克风固定 48k
#     "bit_size": pyaudio.paInt16,
#     "device_index": 3,  # 你的麦克风索引
# }

# 你的设备是 48k，这里按 48k 配置；真正发给大模型前会重采样到 16k 单声道
input_audio_config = {
    # 这个 chunk 现在对我们来说只是一条“配置参考”，真正读数据是从 /audio/audio
    # 可以设成一帧 0.1s 的样本数：48000 * 0.1 = 4800
    "chunk": 4800,
    "format": "pcm",
    "channels": 2,  # *** 注意：一定要和 audio_capture 的 channels 一致 ***
    "sample_rate": 48000,  # *** 注意：和 audio_capture 的 sample_rate 一致 ***
    "bit_size": pyaudio.paInt16,
    "device_index": None,  # 现在不再用 PyAudio 采麦，可设 None
}

# ============ 输出音频（扬声器，经 ROS1 发送到下位机） ============
# 关键：mode = "ros1" -> 使用我们实现的 Ros1SpeakerStream，把“原始 PCM 字节”发布到话题
# 下位机需要按 24k / 单声道 / PCM（常见为 s16le）进行播放
output_audio_config = {
    "chunk": 3200,  # 供本地 PyAudio 使用的缓冲大小；ROS 模式下不影响发布
    "format": "pcm",
    "channels": 1,
    "sample_rate": 24000,  # 与 start_session_req.tts.audio_config 保持一致
    # 对于本地 PyAudio 播放：bit_size 要与下行位宽一致
    # 你之前用的是 paFloat32，这里保持原样；若服务端确认为 s16le，建议改为 pyaudio.paInt16
    "bit_size": pyaudio.paFloat32,
    "device_index": None,  # 仅在 mode='pyaudio' 时生效
    # === 下面这些是“ROS1 扬声器发布”相关的新增字段 ===
    "mode": "ros1",  # 'ros1' 表示通过 ROS 发布音频；改为 'pyaudio' 可切回本地声卡
    "ros1_topic": "/audio",  # 发布的话题名
    "ros1_node_name": "speaker_publisher",  # 发布节点名（进程内自动 init）
    "ros1_queue_size": 10,  # 发布队列
    "ros1_latch": False,  # 音频流不建议 latched，保持 False
}

"""
使用说明：
1) 本文件与 audio_manager.py 中的 AudioDeviceManager/Ros1SpeakerStream 联动：
   - 当 output_audio_config['mode'] == 'ros1' 时，音频播放改为在 ROS 话题发布字节流；
   - 下位机订阅 /robot/speaker/audio 并按 24k/单声道/PCM 解码播放；
   - 若你的下位机采用 audio_common_msgs/AudioData，则消息类型自动为 AudioData；
     若该包未安装，会退化为 std_msgs/ByteMultiArray（字段 data 为 uint8[]），请下位机相应适配。

2) 如需临时切回本地声卡播放：
   - 仅把 output_audio_config['mode'] 改为 'pyaudio'，其他保持不变即可。

3) 若确认服务端返回的是 s16le：
   - 建议把 output_audio_config['bit_size'] 改为 pyaudio.paInt16，以保持一致（仅在 'pyaudio' 模式下有用；
     'ros1' 模式下该字段不参与发布，但建议保持与真实位宽一致，以免后续切回本地时爆音）。
"""
