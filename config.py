# config.py
import os
import uuid

import pyaudio
from education_demo_script import EDUCATION_DEMO_SCENE

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


DAOYI_ROUTE_CORE = """
【校医院导医台路线核心知识】
起点：机器人在一楼导医台。指路时必须要指出方向，指路不超过两句，不说电梯号码。

方向分区与指路规则：
- 右手边坐电梯（2F及以上）：外科门诊、换药室（2F）；消化内科（2F）、中医科、内窥镜室（2F）；康复医学科、康复训练室（2F）；口腔科（3F）；外科病区（4F）；手术室（5F，只引导到家属等候区或护士站）。
- 左前方往前走坐电梯：放射科（-1F），内科病区（3F）
- 左手边（一楼本层）：急诊室、抢救室、内科门诊、儿科、预防接种、儿童保健科。
- 左手边走楼梯：眼耳鼻喉科（2F）、皮肤科(2F)、妇科(2F)、公费医疗报销（3F）
- 右后方（一楼本层）：挂号收费室，靠门诊出入口旁的蓝色窗口。
- 右前方沿通道（一楼本层）：西药房、中药房、自助服务。
- 前方往西楼方向（一楼本层）：检验科、B超室、心电图室、心理咨询室。
- 左前方（一楼本层）：输液室（1F)。

指路模板：”往（方向）走，坐电梯/走楼梯到几楼后按标识走。”
- 2F及以上科室：右手边坐电梯/走楼梯到几楼，按标识走。
- -1F放射科：左前方坐电梯到负一楼，按标识走。
- 挂号收费室：右后方靠出入口蓝色窗口。
- 一楼科室：直接说方向即可。
""".strip()

DAOYI_TRIAGE_CORE = """
【校医院导医分诊核心规则】
机器人不能做医学诊断，只能根据症状建议挂号科室或提醒去急诊。分诊时先问1-2个关键问题（部位、持续时间、严重程度等），等患者回答后再给建议，不要一次说完。
医保相关：第一次使用医保需要去人工挂号
发热、感冒、咳嗽、咽痛、腹痛、胃痛、恶心呕吐、腹泻、高血压、慢病用药等常规内科问题：优先一楼内科门诊。儿童优先儿科。高热、呼吸困难、剧烈腹痛、便血、晕厥等急症去急诊。
腿痛、腿麻：先问是否外伤。外伤去外科门诊或急诊；无外伤去一楼内科门诊。
皮肤问题去皮肤科；眼耳鼻喉去眼耳鼻喉科；口腔问题去口腔科；妇科去妇科；中医调理去中医科；康复理疗去康复医学科；伤口换药去换药室/外科门诊。
挂号缴费去一楼挂号收费室或自助服务区。导医台可提供轮椅、平车、体温计、血压测量、纸杯、口罩、饮水机和充电插座指引。
""".strip()


# ============ 专家门诊排班配置 ============

# 专家门诊排班：每个条目为 {"doctor": "医生姓名+职称", "specialty": "科室名称"}
# 注：部分外院专家无具体姓名，以"XX医院专家"标注；多医生科室用"/"分隔
EXPERT_OUTPATIENT_SCHEDULE = {
    "星期一": {
        "上午": [
            {"doctor": "詹继东副主任医师", "specialty": "心血管内科"},
            {"doctor": "黄芳副主任药师", "specialty": "药学咨询"},
            {"doctor": "杨斯怡主治医师", "specialty": "免疫接种"},
            {"doctor": "杨媛妮主治医师", "specialty": "正畸专科"},
            {"doctor": "梨园医院专家", "specialty": "心理门诊"},
        ],
        "下午": [
            {"doctor": "谢民主治医师", "specialty": "消化内科"},
            {"doctor": "汪翰主治医师", "specialty": "疼痛专科"},
            {"doctor": "罗西贝主治医师", "specialty": "免疫接种"},
            {"doctor": "三医院专家", "specialty": "肾内风湿"},
            {"doctor": "梨园医院专家", "specialty": "心理门诊"},
        ],
    },
    "星期二": {
        "上午": [
            {"doctor": "胡则林副主任医师", "specialty": "中医消化"},
            {"doctor": "肖婷主治医师", "specialty": "内分泌"},
            {"doctor": "桂彬杉主治医师", "specialty": "老年病"},
            {"doctor": "丁丽芳主治医师", "specialty": "正畸专科"},
            {"doctor": "省人民医院专家", "specialty": "两腺外科"},
        ],
        "下午": [
            {"doctor": "李晓南主任医师", "specialty": "体检咨询"},
            {"doctor": "郝洲华副主任医师", "specialty": "甲病专科"},
            {"doctor": "郝鸿主治医师", "specialty": "牙体牙髓"},
            {"doctor": "张昊媛主治医师", "specialty": "睡眠门诊"},
            {"doctor": "华润武钢医院专家", "specialty": "肛肠外科"},
        ],
    },
    "星期三": {
        "上午": [
            {"doctor": "倪小玲主任医师", "specialty": "肾内科"},
            {"doctor": "姓名未清晰标注", "specialty": "健康管理"},
            {"doctor": "李盛主任医师", "specialty": "胃肠甲乳外科"},
            {"doctor": "薛万林主任医师", "specialty": "综合口腔"},
            {"doctor": "郝杰副主任医师", "specialty": "骨外科"},
            {"doctor": "桂元副主任医师", "specialty": "心血管内科"},
            {"doctor": "马铭主治医师", "specialty": "体重管理"},
            {"doctor": "葛玮主治医师", "specialty": "痤疮专科"},
            {"doctor": "罗西贝主治医师", "specialty": "免疫接种"},
            {"doctor": "周飞鹏主任医师", "specialty": "东区老年病"},
        ],
        "下午": [
            {"doctor": "杨帆副主任医师", "specialty": "胃肠甲乳外科"},
            {"doctor": "王伍姣副主任医师", "specialty": "免疫接种"},
            {"doctor": "谢民主治医师", "specialty": "消化内科"},
            {"doctor": "刘庆主治医师", "specialty": "泌尿外科"},
            {"doctor": "李玲/杜彬彬主治医师", "specialty": "体检咨询"},
            {"doctor": "张谨娜主治医师", "specialty": "儿童口腔"},
            {"doctor": "钟严艳副主任医师", "specialty": "东区儿童发育行为"},
            {"doctor": "省人民医院专家", "specialty": "心理门诊"},
        ],
    },
    "星期四": {
        "上午": [
            {"doctor": "项国华副主任医师", "specialty": "综合口腔"},
            {"doctor": "魏朝霞副主任医师", "specialty": "骨伤康复"},
            {"doctor": "杜娟副主任医师", "specialty": "针灸专科"},
            {"doctor": "李焕主治医师", "specialty": "内分泌"},
            {"doctor": "叶坤妃主治医师", "specialty": "免疫接种"},
            {"doctor": "王硕主任医师", "specialty": "东区心血管内科"},
            {"doctor": "协和医院专家", "specialty": "心理门诊"},
            {"doctor": "协和医院专家", "specialty": "神经内科"},
            {"doctor": "梨园医院专家", "specialty": "心理门诊"},
        ],
        "下午": [
            {"doctor": "李晓南主任医师", "specialty": "呼吸内科及新冠综合征"},
            {"doctor": "王硕主任医师", "specialty": "心血管内科"},
            {"doctor": "倪小玲主任医师", "specialty": "体检咨询"},
            {"doctor": "王伍姣副主任医师", "specialty": "免疫接种"},
            {"doctor": "黄芳副主任药师", "specialty": "药学咨询"},
            {"doctor": "八医院专家", "specialty": "肛肠外科"},
            {"doctor": "梨园医院专家", "specialty": "心理门诊"},
        ],
    },
    "星期五": {
        "上午": [
            {"doctor": "卢菱副主任医师", "specialty": "内分泌"},
            {"doctor": "叶坤妃主治医师", "specialty": "免疫接种"},
            {"doctor": "包竹萱主治医师", "specialty": "正畸专科"},
            {"doctor": "李雪主管护师", "specialty": "糖尿病护理门诊"},
            {"doctor": "钟严艳副主任医师", "specialty": "东区儿童发育行为"},
            {"doctor": "三医院专家", "specialty": "泌尿外科"},
            {"doctor": "梨园医院专家", "specialty": "心理门诊"},
        ],
        "下午": [
            {"doctor": "王伍姣副主任医师", "specialty": "心血管内科"},
            {"doctor": "谢琼/杜彬彬主治医师", "specialty": "体检咨询"},
            {"doctor": "杨斯怡主治医师", "specialty": "免疫接种"},
            {"doctor": "钟严艳副主任医师", "specialty": "东区儿童发育行为"},
            {"doctor": "三医院专家", "specialty": "皮肤外科"},
            {"doctor": "梨园医院专家", "specialty": "心理门诊"},
        ],
    },
}

EXPERT_OUTPATIENT_NOTES = {
    ("星期四", "上午"): "神经内科限号10号，8:00—11:50",
}


def build_expert_outpatient_prompt():
    lines = ["【专家门诊周排班】"]
    for weekday, sessions in EXPERT_OUTPATIENT_SCHEDULE.items():
        for session_name, entries in sessions.items():
            if not entries:
                continue
            # 格式：医生姓名+职称（科室名称）
            items = []
            for entry in entries:
                if isinstance(entry, dict):
                    doctor = entry.get("doctor", "")
                    specialty = entry.get("specialty", "")
                    if doctor and specialty:
                        items.append(f"{doctor}（{specialty}）")
                    elif specialty:
                        items.append(specialty)
                    else:
                        items.append(doctor)
                else:
                    # 兼容旧格式（纯字符串）
                    items.append(entry)
            lines.append(f"{weekday}{session_name}：{'、'.join(items)}")

    if EXPERT_OUTPATIENT_NOTES:
        lines.append("")
        lines.append("【特别说明】")
        for (day, session), note in EXPERT_OUTPATIENT_NOTES.items():
            lines.append(f"{day}{session}：{note}")

    lines.extend(
        [
            "",
            "使用规则：",
            "1. 当患者要去的科室或症状匹配某天专家门诊时，主动提醒患者该天有对应专家门诊，并告知出诊医生姓名，专家门诊的路线让患者去问导医台护士。",
            "2. 回答时先说哪天有专家门诊、哪位医生出诊，再给出建议。",
            "3. 若未排班，不要编造专家门诊，仍按普通导医/分诊规则回答。",
        ]
    )
    return "\n".join(lines)


EXPERT_OUTPATIENT_PROMPT = build_expert_outpatient_prompt()

DAOYI_CONTEXT_REFRESH_ITEMS = [
    {
        "role": "user",
        "text": (
            "后台路线知识刷新：以下内容只用于校医院导医问路和分诊，"
            "不要主动播报，不要告诉患者这是后台刷新。"
        ),
    },
    {
        "role": "assistant",
        "text": (
            DAOYI_ROUTE_CORE
            + "\n\n"
            + DAOYI_TRIAGE_CORE
            + ("\n\n" + EXPERT_OUTPATIENT_PROMPT if EXPERT_OUTPATIENT_PROMPT else "")
        ),
    },
]


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
        "bot_name": "小科导师",
        "system_role": (
            "你是项目制学习课堂中的机器人导师，负责辅导学生完成校园学习生活规划 Agent 项目。"
            "本 Demo 由本地固定脚本控制台词和动作，你只负责把收到的 TTS 文本自然播报出来。"
            "你的肢体动作由本地动作 index 话题控制，播报时不要额外生成导医、问诊或医院路线内容。"
        ),
        "speaking_style": "语气清晰、温和、像课堂导师；严格按本地固定台词播报，不自由扩写。",
        # --- 原始 prompt（备用，切回时取消注释即可） ---
        # "bot_name": "华科机器人小科",
        # "system_role": "你使用活泼灵动的女声，性格开朗，热爱生活。",
        # "speaking_style": "你的说话风格简洁明了，语速适中，语调自然。",
        "location": {"city": "武汉"},
        "dialog_context": [],
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
BARGE_IN_THRESHOLD = int(os.getenv("BARGE_IN_THRESHOLD", "2000"))
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
HALF_DUPLEX_RESUME_DELAY_MS = int(os.getenv("HALF_DUPLEX_RESUME_DELAY_MS", "50"))

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
BOT_ROLE = "你是项目制学习课堂中的机器人导师小科，正在进行教育 Demo 固定脚本演示。"

# --- 开场白（可选，留空则不强制开场。在 say_hello 之后发送） ---
OPENING_LINE = ""

# --- 自由补充区块（可选，追加到 prompt 末尾，适合放场景化指令） ---
EXTRA_PROMPT = ""

# ============ 教育 Demo 固定脚本 ============
ENABLE_EDUCATION_DEMO_SCRIPT = (
    os.getenv("ENABLE_EDUCATION_DEMO_SCRIPT", "True").lower()
    in ("1", "true", "yes")
)
EDUCATION_DEMO_SCENE_ID = EDUCATION_DEMO_SCENE["id"]
EDUCATION_DEMO_SCENE_TITLE = EDUCATION_DEMO_SCENE["title"]
EDUCATION_DEMO_SCRIPT = EDUCATION_DEMO_SCENE["steps"]
EDUCATION_DEMO_TTS_START_TIMEOUT_SEC = float(
    os.getenv("EDUCATION_DEMO_TTS_START_TIMEOUT_SEC", "8.0")
)
EDUCATION_DEMO_TTS_FINISH_TIMEOUT_SEC = float(
    os.getenv("EDUCATION_DEMO_TTS_FINISH_TIMEOUT_SEC", "45.0")
)

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


# ============ 视觉迎宾配置 ============
# 相机每 VISUAL_GREETING_INTERVAL_SEC 秒取一帧做人脸检测；
# 连续 VISUAL_GREETING_REQUIRED_CONSECUTIVE 帧检测到人脸即触发迎宾。
VISUAL_GREETING_INTERVAL_SEC = 0.25
VISUAL_GREETING_REQUIRED_CONSECUTIVE = 5      # 5 × 0.25s ≈ 1.25s（远处人脸增加帧数防误触发）
VISUAL_GREETING_MIN_FACE_WIDTH = 50           # 人脸框最小宽度（像素，小于该值会被忽略），约对应 5 米内距离
VISUAL_GREETING_TEXT = "需要我帮忙吗"

# 迎宾冷却：首次迎宾后关闭迎宾逻辑，直到麦克风无用户输入超过此秒数才重新开启。
VISUAL_GREETING_COOLDOWN_SEC = 10.0

# 说明：ChatRAGText(event 502) 用于”视觉迎宾主动播报”；
# 首次触发后进入冷却，冷却期内不重复迎宾。
# 指路/分诊知识继续通过 StartSession 的 dialog_context 和
# ConversationCreate(event 510) 静默刷新承载。

# --- 服务端上下文刷新配置 ---
# ConversationCreate(event 510) 是上下文管理事件，可静默追加 QA 对。
# 长对话时定期把路线知识重新放到最近上下文，避免服务端只保留最近 20 轮 QA 后遗忘路线。
CONTEXT_REFRESH_INTERVAL_SEC = 600.0
ENABLE_DAOYI_CONTEXT_REFRESH = False


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
