# config.py
import os
import uuid

import pyaudio

runtime_control_path = "./yuying/a.txt"


# ws_connect_config = {
#     "base_url": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
#     "headers": {
#         "X-Api-App-ID": "5167059820",
#         "X-Api-Access-Key": "spzdT_8qFFeghO2oiFBMaI0p9RbVR35k",
#         "X-Api-Resource-Id": "volc.speech.dialog",
#         "X-Api-App-Key": "PlgvMymc7f3tQnJ6",
#         "X-Api-Connect-Id": str(uuid.uuid4()),
#     },
# }

ws_connect_config = {
    "base_url": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
    "headers": {
        "X-Api-App-ID": "2770633396",
        "X-Api-Access-Key": "jIKvafY5871Q_Vm5k2aqncpf81ZvvAFY",
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
    # 这个 chunk 现在对我们来说只是一条"配置参考"，真正读数据是从 /audio/audio
    # 可以设成一帧 0.1s 的样本数：48000 * 0.1 = 4800
    "chunk": 4800,
    "format": "pcm",
    "channels": 2,  # *** 注意：一定要和 audio_capture 的 channels 一致 ***
    "sample_rate": 48000,  # *** 注意：和 audio_capture 的 sample_rate 一致 ***
    "bit_size": pyaudio.paInt16,
    # 当不走 PyAudio 采集时仍可保留 None；若需强制指定麦克风，填入名称关键词即可
    "device_name": None,  # 例如 "USB Microphone"（模糊匹配，优先于 device_index）
    "device_index": None,  # 现在不再用 PyAudio 采麦，可设 None
}

# ============ 输入模式选择（麦克风来源） ============
# "ros1" → 从 ROS 话题 /audio/audio 获取音频（需 audio_capture 节点）
# "pyaudio" → 使用本地 PyAudio 直接采集麦克风（适合无 ROS 环境的 Linux 机器）
INPUT_AUDIO_MODE = os.getenv("INPUT_AUDIO_MODE", "pyaudio")

# PyAudio 本地麦克风采集专用配置（INPUT_AUDIO_MODE="pyaudio" 时生效）
pyaudio_input_audio_config = {
    "chunk": 4800,  # 0.1s @ 48k
    "format": "pcm",
    "channels": 1,  # 本地麦克风通常为单声道（与 ROS 模式的 2 声道不同）
    "sample_rate": 48000,  # 大多数设备支持 48k，下游会重采样到 16k
    "bit_size": pyaudio.paInt16,
    "device_name": None,  # None = Linux 系统默认麦克风（PulseAudio/ALSA）
    "device_index": None,  # 可指定具体设备索引；None 时由 PyAudio 选默认设备
}


def get_active_input_config():
    """根据 INPUT_AUDIO_MODE 返回对应的输入音频配置字典。"""
    if INPUT_AUDIO_MODE == "pyaudio":
        return pyaudio_input_audio_config
    return input_audio_config

# ============ 输出音频（扬声器，经 ROS1 发送到下位机） ============
# 关键：mode = "ros1" -> 使用我们实现的 Ros1SpeakerStream，把"原始 PCM 字节"发布到话题
# 下位机需要按 24k / 单声道 / PCM（常见为 s16le）进行播放
# 提示：将 OUTPUT_AUDIO_MODE 环境变量设为 pyaudio/ros1 可在运行时切换输出路径。
OUTPUT_AUDIO_MODE = os.getenv("OUTPUT_AUDIO_MODE", "ros1")
output_audio_config = {
    "chunk": 3200,  # 供本地 PyAudio 使用的缓冲大小；ROS 模式下不影响发布
    "format": "pcm",
    "channels": 1,
    "sample_rate": 24000,  # 与 start_session_req.tts.audio_config 保持一致
    # 对于本地 PyAudio 播放：bit_size 要与下行位宽一致
    # 你之前用的是 paFloat32，这里保持原样；若服务端确认为 s16le，建议改为 pyaudio.paInt16
    "bit_size": pyaudio.paFloat32,
    # 当 mode="pyaudio" 时可用名称模糊匹配声卡输出；优先级高于 device_index。
    "device_name": None,  # 例如 "Realtek" / "Speakers"（大小写不敏感、子串匹配）
    "device_index": None,  # 仅在 mode='pyaudio' 时生效
    # === 下面这些是"ROS1 扬声器发布"相关的新增字段 ===
    "mode": OUTPUT_AUDIO_MODE,  # 'ros1' 经 ROS 发布；设为 'pyaudio' 切回本地扬声器
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

添加扬声器功能：
    新增输出切换：支持用环境变量或配置切换到本地扬声器。PowerShell 运行本地播放示例：$env:OUTPUT_AUDIO_MODE='pyaudio'; python main.py；恢复 ROS：$env:OUTPUT_AUDIO_MODE='ros1'; python main.py。也可以直接改 config.py 的 output_audio_config["mode"]。
新增按设备名选取索引：AudioConfig 增加 device_name，AudioDeviceManager 会在 device_index 为空时按子串（不区分大小写）模糊匹配 PyAudio 设备并填入索引，分别支持输入/输出。
    在 config.py 中填写：麦克风 input_audio_config["device_name"] = "USB Microphone"（示例），扬声器 output_audio_config["device_name"] = "Realtek"。若同时写了 device_index，索引用作更高优先级。
变更文件：audio_constants.py（device_name 字段），audio_device_manager.py（模糊匹配设备名并应用），config.py（新增 device_name 字段、环境变量切换输出模式）。
    下一步可选：在实际环境下运行一次，确认用 pyaudio 输出时选到期望的设备（查看控制台日志或用 PyAudio 枚举工具打印设备列表）。
"""
