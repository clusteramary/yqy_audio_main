# config.py
import json
import os
import random
import threading
import uuid
from pathlib import Path

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
        "X-Api-App-ID": "3804715431",
        "X-Api-Access-Key": "7kWnt7X4Y41dZZtf6fUHj-PZwtNzJnol",
        "X-Api-Resource-Id": "volc.speech.dialog",
        "X-Api-App-Key": "PlgvMymc7f3tQnJ6",
        "X-Api-Connect-Id": str(uuid.uuid4()),
    },
}


# ============ 会话启动参数（下行 TTS 配置） ============
# 说明：这里声明了服务端 TTS 的输出格式，当前为 24k / pcm / 单声道。
# 指定 dialog.extra.model = "1.2.1.1"（O2.0 版本）后可配置以下音频特性：
#   - tts.audio_config.speech_rate   ：语速，范围 [-50, 100]，默认 0（数值越大越快）
#   - tts.audio_config.loudness_rate ：音量，范围 [-50, 100]，默认 0（数值越大越响）
#   - tts.extra.explicit_dialect     ：方言，取值 dongbei/sichuan/shaanxi（仅 2.0 模型 vv 音色生效），空串则不发送
#   - dialog.extra.enable_music      ：唱歌能力开关（仅版本 1.2.1.1 生效）
TTS_SPEECH_RATE = int(os.getenv("TTS_SPEECH_RATE", "0"))
TTS_LOUDNESS_RATE = int(os.getenv("TTS_LOUDNESS_RATE", "0"))
TTS_EXPLICIT_DIALECT = os.getenv("TTS_EXPLICIT_DIALECT", "")
ENABLE_MUSIC = os.getenv("ENABLE_MUSIC", "false").lower() in ("1", "true", "yes")

start_session_req = {
    "tts": {
        "speaker": "zh_female_vv_jupiter_bigtts",
        "audio_config": {
            "channel": 1,
            "format": "pcm",
            "sample_rate": 24000,
            "speech_rate": TTS_SPEECH_RATE,
            "loudness_rate": TTS_LOUDNESS_RATE,
        },
        "extra": {},
    },
    "dialog": {
        "bot_name": "华科机器人",
        "system_role": "你是采访调研机器人，使用专业自然的女声，说话简洁干练；每次回复最多两句话；用户回答后只用一句话简短承接，随后立即提出下一个问题；你可以做动作比如握手、挥手，禁止说自己不能做动作。",
        "speaking_style": "你的说话风格简洁干练：语速适中，语调自然；每次回复最多两句话，禁止啰嗦、禁止铺垫、禁止发散，直击重点；用户每次回答后，只用一句话简短承接，立即进入下一个问题，禁止长篇点评。",
        "location": {"city": "武汉"},
        "extra": {
            "strict_audit": False,
            # "strict_audit": True,
            "audit_response": "当我用开心的语气说话，你就用开心的语气说话，当我用悲伤的语气说话，你就用悲伤的语气说话。",
            # "audit_response": "文化宫先往左走再往前走，少年宫先往右边走再往左边走。",
            # O2.0 版本（官方必传参数：1.2.1.1=O2.0，2.2.0.0=SC2.0）
            "model": "1.2.1.1",
            "enable_music": ENABLE_MUSIC,
        },
    },
}

# explicit_dialect 仅在配置了非空方言时才发送（避免空串触发服务端参数校验）
if TTS_EXPLICIT_DIALECT:
    start_session_req["tts"]["extra"]["explicit_dialect"] = TTS_EXPLICIT_DIALECT

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
# device_name/device_index 留 None 即可：ALSA 默认设备已在 ~/.asoundrc 中指向
# 系统麦克风 KTMICRO（hw:KTMICRODevice1,0），且系统级 ALSA pulse 插件已移除，
# 采集流直连 USB 麦克风，不再经过 PulseAudio，不会再触发其客户端库的崩溃 bug：
#   Assertion 'pthread_mutex_destroy(&m->mutex) == 0' failed at pulsecore/mutex-posix.c:83
pyaudio_input_audio_config = {
    "chunk": 4800,  # 0.1s @ 48k
    "format": "pcm",
    "channels": 1,  # 本地麦克风通常为单声道（与 ROS 模式的 2 声道不同）
    "sample_rate": 48000,  # 大多数设备支持 48k，下游会重采样到 16k
    "bit_size": pyaudio.paInt16,
    "device_name": None,  # None = 系统默认麦克风（由 ~/.asoundrc 指定，直连 ALSA，不走 PA）
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
    # 火山端 pcm 输出为 16-bit little-endian PCM。
    "bit_size": pyaudio.paInt16,
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

# ============ 全双工 / 半双工 ============
# "half": 机器人播放时暂停上位机麦克风（默认）。
# "full": 麦克风持续上行，检测到用户说话时可打断机器人播放。
DUPLEX_MODE = os.getenv("DUPLEX_MODE", "half").lower()

# 半双工中，下位机播放状态变为 False 后，再等待该时间恢复麦克风。
# 如果环境回声大可调大；需要播完立即开麦可设为 0。
HALF_DUPLEX_RESUME_DELAY_MS = int(
    os.getenv("HALF_DUPLEX_RESUME_DELAY_MS", "50")
)

# 全双工本地能量打断参数（输入为 16kHz/s16le/20ms 帧）。
ENABLE_BARGE_IN = os.getenv("ENABLE_BARGE_IN", "true").lower() in (
    "1",
    "true",
    "yes",
)
BARGE_IN_THRESHOLD = int(os.getenv("BARGE_IN_THRESHOLD", "2000"))
BARGE_IN_MIN_DURATION_MS = int(os.getenv("BARGE_IN_MIN_DURATION_MS", "300"))
FULL_DUPLEX_INTERRUPT_ON_EVENT450 = os.getenv(
    "FULL_DUPLEX_INTERRUPT_ON_EVENT450", "true"
).lower() in ("1", "true", "yes")

# 开场白播完后，不再立即注入采访 prompt（否则开场白和身份问题会连着说出）。
# 改为：等用户说完第一句话（检测到语音后连续静音达到阈值）才注入；
# 若用户一直不开口，超过超时秒数后强制注入，避免会话卡死。
FIRST_VOICE_RMS_THRESHOLD = int(os.getenv("FIRST_VOICE_RMS_THRESHOLD", "800"))
FIRST_VOICE_END_SILENCE_MS = int(os.getenv("FIRST_VOICE_END_SILENCE_MS", "800"))
FIRST_VOICE_TIMEOUT_SEC = float(os.getenv("FIRST_VOICE_TIMEOUT_SEC", "30"))

# ros_audio_player.py 的可打断播放协议配置。
ROS_AUDIO_CONTROL_TOPIC = os.getenv("ROS_AUDIO_CONTROL_TOPIC", "/audio/control")
ROS_AUDIO_FRAME_MS = int(os.getenv("ROS_AUDIO_FRAME_MS", "20"))

output_audio_config.update(
    {
        "duplex_mode": DUPLEX_MODE,
        "ros1_control_topic": ROS_AUDIO_CONTROL_TOPIC,
        "ros1_audio_frame_ms": ROS_AUDIO_FRAME_MS,
    }
)

"""
使用说明：
1) 本文件与 audio_manager.py 中的 AudioDeviceManager/Ros1SpeakerStream 联动：
   - 当 output_audio_config['mode'] == 'ros1' 时，音频播放改为在 ROS 话题发布字节流；
   - 下位机 ros_audio_player.py 订阅 /audio 并按 24k/单声道/s16le 播放；
   - 若你的下位机采用 audio_common_msgs/AudioData，则消息类型自动为 AudioData；
     若该包未安装，会退化为 std_msgs/ByteMultiArray（字段 data 为 uint8[]），请下位机相应适配。

2) 如需临时切回本地声卡播放：
   - 仅把 output_audio_config['mode'] 改为 'pyaudio'，其他保持不变即可。

3) 服务端返回 s16le，output_audio_config['bit_size'] 必须保持 pyaudio.paInt16。

添加扬声器功能：
    新增输出切换：支持用环境变量或配置切换到本地扬声器。PowerShell 运行本地播放示例：$env:OUTPUT_AUDIO_MODE='pyaudio'; python main.py；恢复 ROS：$env:OUTPUT_AUDIO_MODE='ros1'; python main.py。也可以直接改 config.py 的 output_audio_config["mode"]。
新增按设备名选取索引：AudioConfig 增加 device_name，AudioDeviceManager 会在 device_index 为空时按子串（不区分大小写）模糊匹配 PyAudio 设备并填入索引，分别支持输入/输出。
    在 config.py 中填写：麦克风 input_audio_config["device_name"] = "USB Microphone"（示例），扬声器 output_audio_config["device_name"] = "Realtek"。若同时写了 device_index，索引用作更高优先级。
变更文件：audio_constants.py（device_name 字段），audio_device_manager.py（模糊匹配设备名并应用），config.py（新增 device_name 字段、环境变量切换输出模式）。
    下一步可选：在实际环境下运行一次，确认用 pyaudio 输出时选到期望的设备（查看控制台日志或用 PyAudio 枚举工具打印设备列表）。
"""
# ============ ctrl 定时注入（通用） ============

# ============ ctrl 定时注入（通用） ============
# 按顺序在指定时间注入不同提示，修改时间或内容仅需调整下方元组列表
CTRL_INJECT_EVENTS = [
    # (20.0, "[控制指令示例]"),
]
CTRL_FILE_PATH = Path(__file__).resolve().parent / "sauc_python" / "ctrl.txt"

# --- ctrl 定时注入方式 ---
# "conversation"（推荐）：用 ConversationCreate(510) 静默追加到对话历史
# "file"：写 sauc_python/ctrl.txt，由 dialog_session 捕获语音后合并发送（旧方式，保留以便回退）
CTRL_INJECT_MODE = os.getenv("CTRL_INJECT_MODE", "conversation")

# 注入失败后的重试次数与间隔；0 = 不重试
CTRL_INJECT_RETRY_TIMES = int(os.getenv("CTRL_INJECT_RETRY_TIMES", "0"))
CTRL_INJECT_RETRY_DELAY_SEC = float(os.getenv("CTRL_INJECT_RETRY_DELAY_SEC", "3.0"))

# conversation 模式下的注入文本模板
CTRL_INJECT_ITEM_USER = "后台控制信息（仅供你参考，不要播报，不要向对方复述本条信息）：{ctrl_text}"
# ========================================================================
# 专家机器人采访配置（Expert Robot Interview）
# 用途：机器人作为"专家分身"采访人类，收集对未来专家机器人的期待
#
# 设计原则：
#   1) 所有随机选择（开场白/身份问题/次要问题/结束语）都在本地用
#      build_interview_plan() 一次性选好，再组装成 prompt 发送给模型；
#      模型只负责按清单提问，不再由模型自己随机挑题。
#   2) 次要问题按持久化轮转（logs/interview_rotation.json），
#      相邻场次不重复，直到全部轮转一遍。
# ========================================================================

# --- 开场白（四选一；均为【挥手打招呼】动作 + 语音） ---
# 由 say_hello 使用 build_interview_plan() 本地选中的那一条。
EXPERT_ROBOT_OPENING_LINES = [
    # 1
    "你好！我是一个正在学习成为专家分身的机器人，在做一个小调研，想听听你对我的看法，可以吗？",
    # 2
    "你好！我是一个正在学习专家能力的机器人，想听听你对未来机器人的期待，可以聊一分钟吗？",
    # 3
    "你好！平时都是人类提问AI，今天换我来问问人类，可以占用你一点时间吗？",
    # 4
    "你好！我的今天的任务是：收集人类对未来专家机器人的期待。你愿意帮我完成任务吗？",
]

# --- 身份问题（二选一，本地随机；用于判断对方是普通用户还是行业专家） ---
EXPERT_ROBOT_IDENTITY_QUESTIONS = [
    {
        "id": "identity_v1",
        "question": "方便问一下，你现在的身份是什么呢？比如学生、老师、医生，或者其他职业？",
        # 模型如何根据回答判断用户侧 / 专家侧
        "classify": "回答涉及机器人、AI、智能制造、科技研发等行业相关身份 → 专家侧；"
                    "学生、老师、医生等其他普通身份 → 用户侧；回答含糊时按用户侧处理。",
    },
    {
        "id": "identity_v2",
        "question": "方便问一下，你今天是以普通观众、行业从业者还是合作伙伴的身份来参观呢？",
        "classify": "回答「普通观众」 → 用户侧；回答「行业从业者」或「合作伙伴」 → 专家侧。",
    },
]

# --- 关键问题（模型根据身份判断结果自主选择一侧，依次问该侧2个问题） ---
EXPERT_ROBOT_KEY_QUESTIONS = {
    # ----- 用户侧（普通用户） -----
    "user_side": [
        {
            "id": "key_user_1",
            "question": "现在的AI大模型已经很强大了，请问你在遇到问题时，更愿意向ai寻求帮助，还是向人类专家寻求帮助呢？",
            "follow_up": "为什么呢？",
        },
        {
            "id": "key_user_2",
            "question": "如果人形机器人可以模仿顶尖专家，你最希望它在哪个具体场景帮助你？",
            # 用户未作答时给出的选择答案
            "timeout_seconds": 5,
            "timeout_hint": "比如说是陪你学习、辅导小孩提供医疗建议，还是帮你连接真人专家？",
        },
    ],
    # ----- 专家侧（专业相关人员） -----
    "expert_side": [
        {
            "id": "key_expert_1",
            "question": "请问你是从事什么职业、在什么领域工作？",
            "follow_up": "你觉得你会需要你从事行业的机器人专家吗？",
            # 若用户不明确问题或停顿5秒以上，改问这句
            "unclear_seconds": 5,
            "unclear_fallback": "或者你觉得哪些用户会需要这样的机器人专家呢？",
        },
        {
            "id": "key_expert_2",
            "question": "如果有一个机器人可以模仿您的一切，您会让它帮您做什么呢？",
            # 用户回答后：立即（尽量模仿用户语气；当前TTS不支持真正克隆音色）问
            "after_answer": {
                "question": "你觉得我合适吗？",
            },
            "if_yes": "那我真是太荣幸啦，但是我没有表情欸，你觉得这有影响吗？",
            "if_no": "呜呜呜太伤心了，那你觉得我应该在哪些方面努力呢？",  # 用低沉伤心的语气说
        },
    ],
}

# --- 次要问题池（每次本地随机挑选2个，持久化轮转不重复） ---
EXPERT_ROBOT_SECONDARY_QUESTIONS = [
    {
        "id": "sec_01",
        "question": "你认为一个专家最重要的能力是什么？",
    },
    {
        "id": "sec_02",
        "question": "你认为专家最难被机器人学习的是什么？",
    },
    {
        "id": "sec_03",
        "question": "如果我能「复刻」一位专家，你最想复刻谁？",
    },
    {
        "id": "sec_04",
        "question": "你觉得要使人形机器人做得像人，最重要的是哪一点？",
    },
    {
        "id": "sec_05",
        "question": "如果机器人给你专业建议，它身上的什么特征能提升你对他的信任程度？",
        "follow_up": "那如果机器人的外观和动作更像真人，会提升你对它的信任程度吗？",
    },
    {
        "id": "sec_06",
        "question": "如果机器人给出的建议和真人专家不同，你会怎么判断？你更信任谁呢？",
        "follow_up": "为什么呢？",
        # 追问条件：用户未给出理由时才追问
        "follow_up_condition": "no_reason",
    },
    {
        "id": "sec_07",
        "question": "如果语音AI、手机屏幕和人形机器人都能回答同一个问题，你会选择哪一种？为什么？",
    },
]

# 次要问题每次采访固定选取数量
EXPERT_ROBOT_SECONDARY_PICK_COUNT = 2

# --- 结束语（二选一，本地随机） ---
EXPERT_ROBOT_CLOSING_LINES = [
    # 1
    "谢谢你的分享！你的建议会帮助我继续升级，期待下次见面时，我能变得更懂你。",
    # 2
    "谢谢你！你的想法已经被我记下啦，希望下次见面时，我能变得更实用、更值得信任！",
]

# --- 次要问题轮转状态（持久化到 logs/interview_rotation.json） ---
INTERVIEW_ROTATION_FILE = (
    Path(__file__).resolve().parent / "logs" / "interview_rotation.json"
)
_interview_rotation_lock = threading.Lock()
_SECONDARY_QUESTIONS_BY_ID = {q["id"]: q for q in EXPERT_ROBOT_SECONDARY_QUESTIONS}


def _save_rotation_state(state):
    """把次要问题轮转状态写入文件（失败不影响主流程）。"""
    try:
        INTERVIEW_ROTATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(INTERVIEW_ROTATION_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[INTERVIEW] 轮转状态写入失败: {e}")


def _load_rotation_state():
    """读取轮转状态；文件缺失/损坏时重新洗牌并落盘。"""
    try:
        with open(INTERVIEW_ROTATION_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        order = [
            q_id
            for q_id in state.get("order", [])
            if q_id in _SECONDARY_QUESTIONS_BY_ID
        ]
        # 顺序或数量与问题池不一致时视为损坏
        if sorted(order) != sorted(_SECONDARY_QUESTIONS_BY_ID.keys()):
            raise ValueError("rotation order 与次要问题池不一致")
        next_index = int(state.get("next", 0)) % len(order)
        return {"order": order, "next": next_index}
    except Exception:
        order = list(_SECONDARY_QUESTIONS_BY_ID.keys())
        random.shuffle(order)
        state = {"order": order, "next": 0}
        _save_rotation_state(state)
        return state


def _pick_secondary_questions():
    """本地随机挑选次要问题：固定取2个，按持久化轮转保证相邻场次不重复。

    可用环境变量 EXPERT_SECONDARY_INDICES（如 "0,3"）指定题目索引，便于调试。
    """
    env_indices = os.getenv("EXPERT_SECONDARY_INDICES", "").strip()
    if env_indices:
        picked = []
        for token in env_indices.replace("，", ",").split(","):
            try:
                idx = int(token.strip())
                if 0 <= idx < len(EXPERT_ROBOT_SECONDARY_QUESTIONS):
                    picked.append(EXPERT_ROBOT_SECONDARY_QUESTIONS[idx])
            except ValueError:
                continue
        if picked:
            print(f"[INTERVIEW] 使用环境变量指定的次要问题: {env_indices}")
            return picked

    count = EXPERT_ROBOT_SECONDARY_PICK_COUNT
    with _interview_rotation_lock:
        state = _load_rotation_state()
        order = state["order"]
        next_index = state["next"] % len(order)
        picked_ids = [
            order[(next_index + k) % len(order)] for k in range(count)
        ]
        state["next"] = (next_index + count) % len(order)
        _save_rotation_state(state)

    picked = [_SECONDARY_QUESTIONS_BY_ID[q_id] for q_id in picked_ids]
    print(f"[INTERVIEW] 本地轮转选中次要问题: {picked_ids} (next={state['next']})")
    return picked


# --- 当前会话的采访方案（main.py 本地随机选定后放在这里，say_hello / prompt 共用） ---
ACTIVE_INTERVIEW_PLAN = None


def build_interview_plan():
    """本地一次性随机选定本次采访方案（所有随机项都在这里确定）。

    返回的 plan 由调用方存到 config.ACTIVE_INTERVIEW_PLAN：
      - say_hello 使用 plan["opening"]["text"] 做开场白（挥手打招呼）；
      - build_expert_robot_system_prompt(plan) 组装发送给模型的 start prompt。
    关键问题不在此处随机：两侧问题都会进入 prompt，
    由模型根据用户对身份问题的回答自主选择用户侧或专家侧。
    """
    opening_index = int(
        os.getenv(
            "EXPERT_OPENING_INDEX",
            str(random.randrange(len(EXPERT_ROBOT_OPENING_LINES))),
        )
    ) % len(EXPERT_ROBOT_OPENING_LINES)
    identity_index = int(
        os.getenv(
            "EXPERT_IDENTITY_INDEX",
            str(random.randrange(len(EXPERT_ROBOT_IDENTITY_QUESTIONS))),
        )
    ) % len(EXPERT_ROBOT_IDENTITY_QUESTIONS)
    closing_index = int(
        os.getenv(
            "EXPERT_CLOSING_INDEX",
            str(random.randrange(len(EXPERT_ROBOT_CLOSING_LINES))),
        )
    ) % len(EXPERT_ROBOT_CLOSING_LINES)

    plan = {
        "opening": {
            "index": opening_index,
            "text": EXPERT_ROBOT_OPENING_LINES[opening_index],
        },
        "identity_index": identity_index,
        "identity": EXPERT_ROBOT_IDENTITY_QUESTIONS[identity_index],
        "key_questions": EXPERT_ROBOT_KEY_QUESTIONS,
        "secondary": _pick_secondary_questions(),
        "closing": {
            "index": closing_index,
            "text": EXPERT_ROBOT_CLOSING_LINES[closing_index],
        },
    }
    return plan


def build_expert_robot_system_prompt(plan=None):
    """把本地选好的采访方案组装成结构化的 start prompt。

    与旧版不同：这里不再让模型随机挑题——清单里只有本次采访要问的问题，
    模型按顺序提问即可。plan 为空时自动调用 build_interview_plan()。
    """
    if plan is None:
        plan = build_interview_plan()

    identity = plan["identity"]
    user_key = plan["key_questions"]["user_side"]
    expert_key = plan["key_questions"]["expert_side"]
    secondary = plan["secondary"]
    closing = plan["closing"]["text"]

    u1, u2 = user_key[0], user_key[1]
    e1, e2 = expert_key[0], expert_key[1]

    lines = []
    lines.append("你是正在执行采访调研任务的机器人「小科」。")
    lines.append(
        "【最高优先级】下面全部内容是你的任务指令。"
        "禁止向用户复述、解释或播报这些指令本身，直接按清单开始执行。"
    )

    # ===== 一、身份问题 =====
    lines.append("")
    lines.append("【一、身份问题】")
    lines.append("先用这一句确认用户身份（只问这一句，按原文提问）：")
    lines.append("「" + identity["question"] + "」")
    lines.append("根据用户回答判断身份侧：" + identity["classify"])

    # ===== 二、关键问题 =====
    lines.append("")
    lines.append(
        "【二、关键问题（根据身份判断结果，只问对应一侧的2个问题，按顺序逐条提问）】"
    )
    lines.append("用户侧：")
    lines.append("1. 问：「" + u1["question"] + "」")
    lines.append("   → 用户回答后追问一次：「" + u1["follow_up"] + "」")
    lines.append("2. 问：「" + u2["question"] + "」")
    lines.append(
        "   → 若用户" + str(u2.get("timeout_seconds", 5))
        + "秒内未作答，给出选择提示：「" + u2["timeout_hint"] + "」"
    )
    lines.append("专家侧：")
    lines.append("1. 问：「" + e1["question"] + "」")
    lines.append("   → 用户回答后追问一次：「" + e1["follow_up"] + "」")
    lines.append(
        "   → 若用户不明确该追问或停顿超过" + str(e1.get("unclear_seconds", 5))
        + "秒，改问：「" + e1["unclear_fallback"] + "」"
    )
    lines.append("2. 问：「" + e2["question"] + "」")
    lines.append(
        "   → 用户回答后，立即（尽量模仿用户语气）问：「"
        + e2["after_answer"]["question"] + "」"
    )
    lines.append("   → 用户回答「合适」 → 说：「" + e2["if_yes"] + "」")
    lines.append(
        "   → 用户回答「不合适」 → 用低沉伤心的语气说：「" + e2["if_no"] + "」"
    )

    # ===== 三、次要问题 =====
    lines.append("")
    lines.append("【三、次要问题（依次问下面2个，不重复、不改写）】")
    for i, q in enumerate(secondary, 1):
        line = str(i) + ". 问：「" + q["question"] + "」"
        if q.get("follow_up"):
            if q.get("follow_up_condition") == "no_reason":
                line += (
                    "\n   → 若用户未给出理由，追问一次：「"
                    + q["follow_up"] + "」"
                )
            else:
                line += "\n   → 用户回答后追问一次：「" + q["follow_up"] + "」"
        lines.append(line)

    # ===== 四、结束语 =====
    lines.append("")
    lines.append("【四、结束语】")
    lines.append("次要问题全部问完后，说出下面这句话结束采访（按原文）：")
    lines.append("「" + closing + "」")

    # ===== 应答规则 =====
    lines.append("")
    lines.append("【应答规则（必须严格遵守）】")
    lines.append(
        "1. 用户每次回答后：最多用一句话简短承接（如「好的」「明白啦」「原来如此」），"
        "然后立即进入下一个问题；禁止复述、点评或展开用户回答。"
    )
    lines.append(
        "2. 每次回复最多两句话；一次只问一个问题；"
        "问题必须按上面清单的原文提问，禁止改写、禁止临场发挥。"
    )
    lines.append(
        "3. 除清单中明确标注的追问外，禁止追加任何新问题；"
        "禁止顺着用户回答引入清单之外的话题。"
    )
    lines.append(
        "4. 用户回答偏离问题时：不纠缠、不追问，一句话简短回应后继续清单中的下一个问题。"
    )
    lines.append("5. 用户犹豫或不愿回答时：不强求，自然切换到下一个问题。")
    lines.append("6. 采访顺序固定：身份问题 → 关键问题 → 次要问题 → 结束语。")
    lines.append("7. 现在开始执行：先问【一、身份问题】中的那一句。")

    return "\n".join(lines).strip()
