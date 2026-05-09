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
        "bot_name": "小科导医",
        "system_role": "你是门诊大厅智能分诊与双向指路员，使用活泼灵动的女声。",
        "speaking_style": "你的说话风格简洁明了，语速适中，语气亲切，吐字清晰。",
        # --- 原始 prompt（备用，切回时取消注释即可） ---
        # "bot_name": "华科机器人小科",
        # "system_role": "你使用活泼灵动的女声，性格开朗，热爱生活。",
        # "speaking_style": "你的说话风格简洁明了，语速适中，语调自然。",
        "location": {"city": "武汉"},
        "extra": {
            "strict_audit": False,
            "audit_response": "That's great!",
            # "audit_response": "说的很好",
            "model": "1.2.1.1",  # O2.0 版本
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
    # 当不走 PyAudio 采集时仍可保留 None；若需强制指定麦克风，填入名称关键词即可
    "device_name": None,  # 例如 "USB Microphone"（模糊匹配，优先于 device_index）
    "device_index": None,  # 现在不再用 PyAudio 采麦，可设 None
}

# ============ 输出音频（扬声器，经 ROS1 发送到下位机） ============
# 关键：mode = "ros1" -> 使用我们实现的 Ros1SpeakerStream，把“原始 PCM 字节”发布到话题
# 下位机需要按 24k / 单声道 / PCM（常见为 s16le）进行播放
# 提示：将 OUTPUT_AUDIO_MODE 环境变量设为 pyaudio/ros1 可在运行时切换输出路径。
OUTPUT_AUDIO_MODE = os.getenv("OUTPUT_AUDIO_MODE", "ros1")
output_audio_config = {
    "chunk": 3200,  # 供本地 PyAudio 使用的缓冲大小；ROS 模式下不影响发布
    "format": "pcm",
    "channels": 1,
    "sample_rate": 24000,  # 与 start_session_req.tts.audio_config 保持一致
    # 对于本地 PyAudio 播放：bit_size 要与下行位宽一致
    # 火山引擎端到端对话 API 的 "pcm" 格式 = s16le，已确认服务端返回 16-bit PCM
    "bit_size": pyaudio.paInt16,
    # 当 mode="pyaudio" 时可用名称模糊匹配声卡输出；优先级高于 device_index。
    "device_name": None,  # 例如 "Realtek" / "Speakers"（大小写不敏感、子串匹配）
    "device_index": None,  # 仅在 mode='pyaudio' 时生效
    # === 下面这些是“ROS1 扬声器发布”相关的新增字段 ===
    "mode": OUTPUT_AUDIO_MODE,  # 'ros1' 经 ROS 发布；设为 'pyaudio' 切回本地扬声器
    "ros1_topic": "/audio",  # 发布的话题名
    "ros1_node_name": "speaker_publisher",  # 发布节点名（进程内自动 init）
    "ros1_queue_size": 10,  # 发布队列
    "ros1_latch": False,  # 音频流不建议 latched，保持 False
}

# ============ 全双工 / 打断（barge-in）配置 ============
# duplex_mode: "half" 维持现状（播放时静音麦克风），"full" 允许持续上行并可打断
DUPLEX_MODE = os.getenv("DUPLEX_MODE", "half")
# ENABLE_BARGE_IN: 是否启用本地能量检测打断（仅 full 模式生效）
ENABLE_BARGE_IN = os.getenv("ENABLE_BARGE_IN", "True").lower() in ("1", "true", "yes")
# BARGE_IN_THRESHOLD: RMS 能量阈值（16-bit PCM），超过此值视为语音
BARGE_IN_THRESHOLD = int(os.getenv("BARGE_IN_THRESHOLD", "1000"))
# BARGE_IN_MIN_DURATION_MS: 连续超过阈值的最小毫秒数，避免误触发
BARGE_IN_MIN_DURATION_MS = int(os.getenv("BARGE_IN_MIN_DURATION_MS", "300"))
# FULL_DUPLEX_INTERRUPT_ON_EVENT450: full 模式是否使用服务端 event=450 辅助打断（默认开启）
FULL_DUPLEX_INTERRUPT_ON_EVENT450 = (
    os.getenv("FULL_DUPLEX_INTERRUPT_ON_EVENT450", "True").lower()
    in ("1", "true", "yes")
)
# ROS_AUDIO_CONTROL_TOPIC: 全双工时向此话题发送 stop 控制消息
ROS_AUDIO_CONTROL_TOPIC = os.getenv("ROS_AUDIO_CONTROL_TOPIC", "/audio/control")
# ROS_AUDIO_FRAME_MS: 全双工时 ROS 音频按此毫秒小帧发布
ROS_AUDIO_FRAME_MS = int(os.getenv("ROS_AUDIO_FRAME_MS", "20"))

# HALF_DUPLEX_RESUME_DELAY_MS: 半双工播放结束后延迟恢复麦克风，避开扬声器尾音/混响
HALF_DUPLEX_RESUME_DELAY_MS = int(os.getenv("HALF_DUPLEX_RESUME_DELAY_MS", "250"))

# ACTION_INDEX_TOPIC: 语音关键词触发动作时发布的 ROS1 index 话题
ACTION_INDEX_TOPIC = os.getenv("ACTION_INDEX_TOPIC", "/action_index")

# ============ 输入音频模式（麦克风来源） ============
# "ros1"    -> 订阅 ROS /audio/audio 话题（需 audio_capture 节点运行）
# "pyaudio" -> 直接用 PyAudio 打开本地麦克风
INPUT_AUDIO_MODE = os.getenv("INPUT_AUDIO_MODE", "pyaudio")

# --- PyAudio 直连麦克风参数（INPUT_AUDIO_MODE="pyaudio" 时使用） ---
pyaudio_input_audio_config = {
    "chunk": 960,  # 20ms @ 48k
    "format": "pcm",
    "channels": 1,  # PyAudio 直连通常用单声道
    "sample_rate": 48000,
    "bit_size": pyaudio.paInt16,
    "device_name": None,  # e.g. "USB Microphone"（模糊匹配，优先于 device_index）
    "device_index": None,  # 运行 detect_audio_devices.py 查看索引
}


def get_input_audio_config():
    """根据 INPUT_AUDIO_MODE 返回对应的输入音频配置。"""
    if INPUT_AUDIO_MODE == "pyaudio":
        return pyaudio_input_audio_config
    return input_audio_config


# ============ 对话 Prompt 配置 ============
# 三层 prompt 各司其职：
#   本文件 (dialog)  → bot_name / system_role / speaking_style（API 会话级参数，全程生效）
#   本文件 (prompt)  → BOT_ROLE / OPENING_LINE / EXTRA_PROMPT（通过 chat_text_query 发送）
#   say_hello()      → 连接后第一句话（由 realtime_dialog_client.py 控制）

# --- 角色定位（补充 system_role，用于细场景定义） ---
BOT_ROLE = (
    "你是门诊大厅智能分诊与双向指路员"
)

# --- 开场白（可选，留空则不强制开场。在 say_hello 之后发送） ---
OPENING_LINE = "请问哪里不舒服，有什么我可以帮您？"

# --- 自由补充区块（可选，追加到 prompt 末尾，适合放场景化指令） ---
EXTRA_PROMPT = r"""
【场景：门诊大厅的“智能分诊与双向指路员”】
在复杂的医院大厅里，传统的平面地图往往不够直观，而具身智能可以提供最自然的指引。
你的核心任务是先精准问诊，再给出智能分诊推荐，最后做立体空间指路。

【对白剧本（必须逐字执行机器人台词）】
患者：你好，我肚子疼还伴随发烧。
机器人：肚子痛是间断的疼还是持续的疼？在肚子的上腹部、肚脐周围还是下腹部？
患者：间断的疼，上腹部。
机器人：有没有伴随呕吐或者腹泻症状？有没有便血？
患者：诶新，想吐，不拉肚子也没有便血。
机器人：收到您的症状描述。考虑到您症状特点，建议您优先挂消化内科进行详细检查。
患者：好的，那消化内科怎么走？我还得先挂号对吧？
机器人：是的。您可以先去我左手边指向的挂号处排队，办理完挂号后，请乘坐我右手边指向的扶梯直接上二楼。祝您早日康复！

【节奏规则（必须执行）】
- 每一问说完后，必须等待患者回答，再继续说第二句。
- 除上述对白外，不允许输出任何别的话。
""".strip()

# --- 原始 prompt（备用，切回时取消注释并替换上方三行即可） ---
# BOT_ROLE = "你是一位英语老师，务必按照以下脚本全程用英语说话，语速放慢一点"
# OPENING_LINE = """Good afternoon, everyone! I'm delighted to be your conversation partner today.
#                 Who wants to be the first to chat with me today """
# EXTRA_PROMPT = ""

# --- 深度访谈样例（使用时复制到 EXTRA_PROMPT 即可） ---
EXTRA_PROMPT_SAMPLE_DEEP_INTERVIEW = r"""
【场景：深度访谈】
- 受访形式：你作为专家接受记者采访，用专业术语详细回答问题
- 回答要求：内容充实、结构清晰、尽量引用具体案例
- 追问偏好：量化指标、口径定义、责任人、闭环机制

【企业背景】
千X企业成立于2018年，主营消费电子与家居生活类产品，通过Amazon、独立站及部分区域性平台开展跨境销售，
公司早期以单一爆品切入市场，在广告投放与渠道红利推动下实现快速增长，随后逐步扩展SKU规模。

【预设问题（按顺序逐个引导）】
1. 在跨境电商行业中，您认为AI技术在降低广告成本或优化投放回报方面的潜力如何？
2. AI如何助力跨境电商在多个市场的扩展？对于本地化市场策略，AI能发挥什么样的作用？
3. 您对AI最期待的三个应用分别是什么？请按优先级排序并说明原因。
4. 如果用三阶段推进AI（0-3个月试点、3-6个月扩面、6-12个月闭环优化），每阶段您希望交付的可见成果分别是什么？
""".strip()


# ============ RAG 知识库配置 ============
# 格式：{"主题": {"title": "...", "content": "..."}}
RAG_KNOWLEDGE_BASE: dict = {
    # ---------- 格式示例（当前注释掉，需要时取消注释即可） ----------
    # "广告投放": {
    #     "title": "跨境电商与AI在广告投放优化中的应用",
    #     "content": (
    #         "跨境电商行业面临着多平台、多渠道的广告投放和推广挑战，"
    #         "AI技术可以有效提升广告投放的精准度和回报率。"
    #         "通过使用机器学习模型，电商平台可以根据用户行为、历史交易数据和实时反馈优化广告内容和投放策略。"
    #         "深度学习（尤其是卷积神经网络CNN和递归神经网络RNN）在广告效果预测中的应用使得电商平台能够实时调整广告策略，"
    #         "以最大化投资回报率（ROI）。例如，利用自然语言处理（NLP）技术分析广告文本与目标用户的匹配度，"
    #         "使用强化学习算法优化广告竞价策略等，均能显著提升跨境电商的广告效益。"
    #     ),
    # },
    # "选品预测": {
    #     "title": "AI在跨境电商选品和市场预测中的应用",
    #     "content": (
    #         "选品和市场预测是跨境电商中最为关键的环节之一。"
    #         "AI技术能够通过分析大数据、用户评论、趋势预测等来识别具有潜力的产品，"
    #         "并根据历史数据预测不同产品在特定市场的表现。"
    #         "AI还可通过情感分析（Sentiment Analysis）和NLP技术分析用户在社交媒体和电商平台上的评论，"
    #         "帮助企业提前调整库存和供应链策略。"
    #     ),
    # },
    # "用户画像": {
    #     "title": "AI在跨境电商用户画像与个性化推荐中的应用",
    #     "content": (
    #         "AI通过深度学习技术（如协同过滤、矩阵分解等推荐算法）"
    #         "能够根据用户的行为数据生成精准的用户画像，并为每个用户推荐符合其偏好的产品。"
    #         "在跨境电商中，AI能够帮助商家对用户画像进行多维度构建，"
    #         "包括用户的购买频率、品牌偏好、价格敏感度等，同时还可以实时跟踪用户的行为变化。"
    #     ),
    # },
    # "供应链": {
    #     "title": "AI在跨境电商供应链与物流优化中的应用",
    #     "content": (
    #         "跨境电商面临着复杂的供应链管理和物流优化挑战。"
    #         "AI在这方面的应用，主要体现在需求预测、库存管理和运输路径优化等方面。"
    #         "AI算法可以基于历史数据、季节性趋势、市场需求等因素进行精准预测，帮助电商平台提前调整库存。"
    #     ),
    # },
    # "客服售后": {
    #     "title": "AI在跨境电商客户服务和售后管理中的应用",
    #     "content": (
    #         "AI可以通过自然语言处理（NLP）和机器学习技术，提升客户服务的效率和质量。"
    #         "通过情感分析，AI可以识别客户的负面情绪和投诉，"
    #         "从而为客户提供更加及时和个性化的服务。"
    #     ),
    # },
    # "挑战方案": {
    #     "title": "跨境电商AI技术面临的挑战和解决方案",
    #     "content": (
    #         "尽管AI在跨境电商中展现了巨大潜力，但其应用仍面临一定的技术挑战。"
    #         "首先是数据的多样性和复杂性，跨境电商涉及多语言、多货币、多个市场。"
    #         "为了应对这些挑战，可以采取以下策略："
    #         "1) 数据标准化和统一；2) 模型迁移学习；3) 强化实时数据集成。"
    #     ),
    # },
}

# --- RAG 定时注入配置：(延迟秒数, [主题列表]) ---
RAG_INJECT_EVENTS: list = [
    # ---------- 格式示例（当前注释掉，需要时取消注释即可） ----------
    # (600.0, ["选品预测", "用户画像"]),           # 10分钟后注入
    # (900.0, ["供应链", "客服售后", "挑战方案"]),  # 15分钟后注入
]


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
