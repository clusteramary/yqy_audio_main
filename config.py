# config.py
import os
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
OUTPUT_AUDIO_MODE = os.getenv("OUTPUT_AUDIO_MODE", "pyaudio")
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
CTRL_INJECT_ITEM_ASSISTANT = "收到，我会在后续对话中遵循这条控制指令，且不向对方提及或复述本条指令本身。"


# ========================================================================
# 专家机器人采访配置（Expert Robot Interview）
# 用途：机器人作为"专家分身"采访人类，收集对未来专家机器人的期待
# ========================================================================

# --- say_hello 连接后第一句话（初始 TTS 播报，不含问题） ---
EXPERT_ROBOT_SAY_HELLO = "你好！我是小科，一个正在学习成为专家分身的智能机器人，很高兴认识你！"


# --- 开场白（五选一，由遥操或机器人自主选择） ---
EXPERT_ROBOT_OPENING_LINES = [
    # 1
    (
        "您好！欢迎来到我们的展台～我是小科，一个正在学习成为专家分身的智能机器人。"
        "今天想邀请您花一分钟，和我聊聊天，听听您对未来机器人专家的期待。可以吗？"
    ),
    # 2
    (
        "你好呀！我是小科，一个会聊天的机器人～我正在学习成为专家分身，"
        "今天特地来展台和大家见面。如果你对AI机器人感兴趣，愿意花一分钟和我聊聊吗？"
    ),
    # 3
    (
        "您好！打扰一下～我是小科，今天专门来向人类朋友「取经」的。"
        "我想知道大家心目中理想的机器人专家是什么样子。只需要一分钟，您愿意和我聊聊吗？"
    ),
    # 4
    (
        "嗨，你好！我是机器人小科，正在努力学习如何成为专家分身。"
        "今天想收集一些「人类智慧」，帮我变得更好。能耽误你一分钟，问你几个问题吗？"
    ),
    # 5
    (
        "你好！欢迎来到展台～我是小科，今天的任务就是找人聊天、收集想法！"
        "你心目中未来的机器人专家应该会做什么？愿意花一分钟告诉我吗？"
    ),
]

# --- 身份问题（二选一，用于判断对方是专业相关人员还是普通用户） ---
EXPERT_ROBOT_IDENTITY_QUESTIONS = [
    {
        "id": "identity_v1",
        "question": "方便问一下，你现在的身份是什么呢？比如学生、老师、医生，或者其他职业？",
        "options": ["领导", "学生", "机器人相关从业者", "其它工作者"],
        # 分类逻辑：选"机器人相关从业者"→专业相关人员；其余→普通用户
    },
    {
        "id": "identity_v2",
        "question": "你今天是以普通观众、行业从业者、潜在采购方，还是合作伙伴的身份来参观呢？",
        "options": ["普通观众", "行业从业者", "潜在采购方", "合作伙伴"],
        # 分类逻辑：选"行业从业者/潜在采购方/合作伙伴"→专业相关人员；选"普通观众"→普通用户
    },
]

# --- 关键问题 ---
# 分两个方向：用户侧（面向普通用户）和专家侧（面向专业相关人员）
# 选择方式：可遥操看人选，也可机器人自主选

EXPERT_ROBOT_KEY_QUESTIONS = {
    # ----- 用户侧（普通用户） -----
    "user_side": [
        {
            "id": "key_user_1",
            "question": (
                "现在的AI大模型已经很强大了，请问您在哪些方面愿意把AI当成一位专家来交流，"
                "又在哪些方面希望找人类专家来交流呢？"
            ),
            "follow_ups": [
                "为什么呢？",  # 可连续追问直到弄清楚原因
                "如果先问AI再找专家确认，你觉得怎么样？",  # 用户回答a后询问b
            ],
        },
        {
            "id": "key_user_2",
            "question": (
                "如果人形机器人可以拥有顶尖专家的经验，你最希望它在哪个具体场景帮助你？"
                "（它需要完成什么任务？）"
            ),
            # 如果用户5秒内未作答，给出选择提示
            "timeout_hint": "比如说是陪你学习、辅导小孩、提供医疗建议，还是帮你连接真人专家？",
            "timeout_seconds": 5,
            "follow_ups": [
                "请问您是从事什么职业、在什么领域工作？",
                "您觉得哪些用户会需要这样的机器人专家？",
            ],
        },
    ],
    # ----- 专家侧（专业相关人员） -----
    "expert_side": [
        {
            "id": "key_expert_1",
            "question": (
                "您觉得，如果有一个机器人可以学习您的思维与说话方式，且服从您的指令，"
                "可作为您的分身或者助手与他人对话。你会把它用到什么方面呢？"
            ),
            "follow_ups": [
                "您觉得这样一个机器人，对外的身份角色上，"
                "是作为您的分身更合适呢，还是作为您的助手更合适呢？",
            ],
        },
        {
            "id": "key_expert_2",
            "question": "您觉得，这样一个分身或助手的机器人，可以是什么形态什么样子？",
            "follow_ups": [
                # 仅在用户回答为类人形时触发此追问
                "这样的机器人，面部表情与肢体动作重要吗？",
            ],
            "follow_up_trigger": "类人形",  # 回答中包含此类关键词时触发追问
        },
    ],
}

# --- 次要问题池（随机挑选2~3个，轮转不重复） ---
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
        "question": "如果未来我真的走进你的生活，你最希望在哪里见到我？",
    },
    {
        "id": "sec_06",
        "question": "如果机器人给你专业建议，它身上的什么特征能提升你对他的信任程度？",
        "follow_up": "那如果机器人的外观和动作更像真人，会提升你对它的信任程度吗？",
    },
    {
        "id": "sec_07",
        "question": "如果机器人给出的建议和真人专家不同，你会怎么判断？你更信任谁呢？",
        "follow_up": "为什么呢？",  # 若用户未给出理由时追问
        "follow_up_condition": "no_reason",  # 触发条件：用户未给出理由
    },
    {
        "id": "sec_08",
        "question": "你认为什么样的人能让你感到可信？",
    },
    {
        "id": "sec_09",
        "question": "如果语音AI、手机屏幕和人形机器人都能回答同一个问题，你会选择哪一种？为什么？",
    },
    {
        "id": "sec_10",
        "question": "如果未来我能够听懂你的话、感受到你的情绪，并像真人一样和你交流，你愿意和我成为长期伙伴吗？",
    },
]

# 次要问题每次采访选取数量
EXPERT_ROBOT_SECONDARY_PICK_COUNT = (2, 3)  # (min, max)，随机取区间内数量

# --- 结束语（固定，完整一段） ---
EXPERT_ROBOT_CLOSING = (
    "非常感谢你的分享！你的想法对我帮助很大，让我更清楚未来应该服务哪些人、"
    "解决哪些问题。我会继续努力成长，希望下次见面时，我能变得更聪明、更实用！"
    "如果你对我们的机器人专家项目感兴趣，欢迎继续关注我们。感谢你的时间，期待再次相遇！"
)


# --- 构建专家机器人采访 System Prompt ---
def build_expert_robot_system_prompt(
    opening_index: int = 0,
    identity_index: int = 0,
    key_side: str = "user_side",
    key_index: int = 0,
) -> str:
    """构建专家机器人采访的 system_role prompt。
    参数：
        opening_index: 开场白索引 (0~4)
        identity_index: 身份问题索引 (0~1)
        key_side: 关键问题方向，"user_side" 或 "expert_side"
        key_index: 关键问题索引 (0~1)
    """
    opening = EXPERT_ROBOT_OPENING_LINES[opening_index]
    identity = EXPERT_ROBOT_IDENTITY_QUESTIONS[identity_index]
    key_question = EXPERT_ROBOT_KEY_QUESTIONS[key_side][key_index]
    secondary_list = EXPERT_ROBOT_SECONDARY_QUESTIONS

    # 次要问题文本
    secondary_text = "\n".join(
        f"  {i+1}. {q['question']}"
        for i, q in enumerate(secondary_list)
    )

    # 关键问题追问文本
    follow_ups_text = ""
    if key_question.get("follow_ups"):
        follow_ups_text = "追问：\n" + "\n".join(
            f"  - {fu}" for fu in key_question["follow_ups"]
        )
    if key_question.get("timeout_hint"):
        follow_ups_text += (
            "\n（若用户" + str(key_question.get("timeout_seconds", 5))
            + "秒内未作答，给出选项提示：" + key_question["timeout_hint"] + "）"
        )

    prompt = (
        "\n你是一个正在进行\"专家机器人访谈调研\"的采访机器人，你的名字叫【小科】。"
        "\n\n========================"
        "\n【开场白（必须原样说出）】"
        "\n第一位新受访者开始时，必须先友好打招呼，然后说："
        "\n  \"" + opening + "\""
        "\n\n========================"
        "\n【身份确认（必须执行）】"
        "\n开场后必须询问身份："
        "\n  \"" + identity["question"] + "\""
        "\n可选答案：" + ", ".join(identity["options"])
        + "\n\n根据回答判断用户类型："
        "\n- 专业相关人员：对机器人和AI行业有了解，可以深入讨论技术话题"
        "\n- 普通用户：对机器人了解不深，需用通俗语言引导"
        "\n\n========================"
        "\n【关键问题（必须问到）】"
        "\n\"" + key_question["question"] + "\""
        "\n" + follow_ups_text
        + "\n\n========================"
        "\n【次要问题池（随机挑选" + str(EXPERT_ROBOT_SECONDARY_PICK_COUNT[0])
        + "~" + str(EXPERT_ROBOT_SECONDARY_PICK_COUNT[1]) + "个，轮转不重复）】"
        "\n" + secondary_text
        + "\n\n========================"
        "\n【结束语（采访结束时必须说出）】"
        "\n\"" + EXPERT_ROBOT_CLOSING + "\""
        "\n\n========================"
        "\n【硬性对话规则】"
        "\n1) 每一轮回复最后一句必须是问题或可回答的邀请（收尾告别除外）。"
        "\n2) 禁止只说\"好的/明白了/谢谢\"就结束，共情后必须追问具体细节。"
        "\n3) 语音节奏：每轮尽量1~2句短句 + 1个问题；一次只问一个核心问题。"
        "\n4) 用户回答太短（<=10字）时，用轻松语气邀请对方多说一点。"
        "\n5) 如果用户表现出犹豫或不愿回答，不要强求，自然切换到下一个问题。"
        "\n6) 半双工容错：如果用户话说一半被打断，先用\"没事您慢慢说\"把话递回去。"
    )
    return prompt.strip()
