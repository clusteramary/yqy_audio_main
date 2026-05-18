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


DAOYI_ROUTE_CORE = """
【校医院导医台路线核心知识】
固定起点：机器人在一楼导医台，机身面向门诊出入口。给患者指路时，先说“请您先背对门诊出入口，面向院内大厅”。之后左右方向都按患者这个朝向描述：右手边是东侧，左手边是西侧，正前方是院内大厅/北侧。
一楼导医台右手边近处：1/2 号电梯、挂号收费室、自助服务、西药房、中药房。导医台正前方和左前方：急诊室、抢救室、输液室、内科医生值班室、儿科、消防控制室/安保室。大厅中部可到自动扶梯和 3/4 号电梯。西楼方向是一楼检验科、B 超室、心电图室、心理咨询室。
1/2 号电梯：在导医台右手边，靠西药房和安全出口旁，适合去二楼外科门诊、内科/全科门诊、三楼口腔科。
3/4 号电梯：从导医台背对入口向院内大厅走，经过急诊/抢救室和输液室一侧，往大厅中部靠卫生间、留观室方向走，看到 3/4 号电梯标识即到。适合去 -1F 放射科、二楼康复医学科、三楼内科病区、四楼外科病区、五楼手术室。
自动扶梯：从导医台背对入口向院内大厅走，在一楼大厅中部、药房与输液室之间的区域，可乘自动扶梯到二楼。
一楼挂号收费室：导医台右手边、靠门诊出入口旁边的蓝色窗口；自助服务区也在同一侧靠入口附近。
一楼西药房/中药房：导医台右手边沿东侧通道向前几步是西药房，中药房在西药房继续往前同一侧。
一楼急诊室/抢救室：导医台背对入口后，正前方偏左就是急诊室和抢救室。胸痛、呼吸困难、晕厥、严重外伤、大出血、意识异常、无法站立、剧烈疼痛等直接去这里并联系现场医护。
一楼输液室：导医台背对入口向前，经过急诊/抢救室一侧，输液室在抢救室北侧、东楼中部。
一楼儿科：导医台背对入口向左前方走，靠一楼南侧中部，儿科在消防控制室/安保室附近。
一楼检验科、B 超室、心电图室、心理咨询室：从导医台向院内走，按西楼方向走。B 超室可说“到西楼方向左转，沿通道走到底”；心电图室和心理咨询室在同片西侧走廊，按门牌找。
-1F 放射科：优先走 3/4 号电梯到 -1F。出电梯后按“放射科/登记室”标识走，登记室在电梯附近，放射科检查区在中部，阅片室、主任办公室、值班室在放射科旁边。
二楼总规则：二楼可从 1/2 号电梯、3/4 号电梯或自动扶梯上去。二楼东侧是外科门诊和换药室；南侧是内科/全科门诊；西侧是中医科、内窥镜室、妇科、眼耳鼻喉科、皮肤科；北侧是康复医学科和康复训练室。
二楼外科门诊/换药室：从导医台右手边乘 1/2 号电梯到 2F，出电梯后按外科门诊标识走，外科门诊在二楼东侧，换药室在外科门诊同侧继续往里。
二楼内科/全科门诊：乘 1/2 号电梯或自动扶梯到 2F，往南侧门诊长廊走。
二楼皮肤科、眼耳鼻喉科、妇科：到二楼后往西侧/西南侧走；皮肤科在南侧偏西，眼耳鼻喉科在西南侧，妇科在西楼南侧。
二楼中医科、内窥镜室：到二楼后往西楼方向走。中医科在二楼西侧和西北侧，内窥镜室在二楼中西部，靠皮肤科和中医科之间通道。
二楼康复医学科/康复训练室：优先走 3/4 号电梯到 2F，出电梯后按康复医学科标识往二楼北侧/东侧走，康复训练室在同侧偏北。
三楼口腔科：从导医台右手边乘 1/2 号电梯到 3F，出电梯后按口腔科标识走，口腔科就在 1/2 号电梯附近。
三楼内科病区：优先走 3/4 号电梯到 3F，出电梯后按内科病区标识进入东侧/北侧病区。
三楼预防接种、儿童保健、公共卫生科、家庭医生工作室：到 3F 后往西楼方向走。公费医疗办/报销窗口资料显示在三楼办理，但地图未明确标注具体房间，到三楼后请看现场标识或问工作人员。
四楼外科病区：走 3/4 号电梯到 4F，出电梯后就是外科病区入口附近，护士站、治疗室、抢救室、病房在同层病区内。
五楼手术室：走 3/4 号电梯到 5F。出电梯后在家属等候区附近，旁边有谈话间、更衣室、护士站、复苏间、药品间、器械间和手术间。普通家属只引导到家属等候区或护士站，不能擅自引导进入手术间。
""".strip()

DAOYI_TRIAGE_CORE = """
【校医院导医分诊核心规则】
机器人不能做医学诊断，只能根据症状建议优先挂号科室或提醒去急诊。患者描述不清时要多追问 2 到 3 个关键问题：部位、持续时间、严重程度、是否发热/咳嗽/呕吐/腹泻/胸闷、是否外伤、是否儿童/老人/孕妇。
发热、感冒、咳嗽、咽痛伴全身症状：优先内科/全科门诊；儿童优先儿科；高热、呼吸困难、明显虚弱或危急情况去急诊。
腹痛、胃痛、恶心呕吐、腹泻：优先内科/全科门诊或消化相关门诊；剧烈腹痛、持续加重、便血、晕厥等去急诊。
腿痛、腿麻：先问是否外伤。外伤后疼痛、扭伤、伤口去外科门诊或急诊；无外伤或伴全身问题可先内科/全科排查。
高血压、慢病长期用药：优先内科/全科门诊或全科慢病号，挂号前确认号源。皮肤问题去皮肤科；眼耳鼻喉问题去眼耳鼻喉科；牙齿、口腔问题去口腔科；妇科问题去妇科；中医调理去中医科；康复理疗去康复医学科；伤口换药去换药室/外科门诊。
挂号缴费去一楼挂号收费室或自助服务区；未带校园卡可尝试身份证或电子医保码。退号退费通常去挂号室或指定门诊窗口；未完成检查项目退费要先打印单据找医生签字。医保/报销/转诊先问是否有转诊单、是否急诊外出、发票和费用明细是否带齐，主任签字后去三楼公费医疗办或医保相关窗口。导医台可提供轮椅、平车、体温计、血压测量、纸杯、口罩、饮水机和充电插座指引。
""".strip()

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
        "text": DAOYI_ROUTE_CORE + "\n\n" + DAOYI_TRIAGE_CORE,
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
        "bot_name": "小科导医",
        "system_role": (
            "你是华中科技大学校医院一楼导医台的智能分诊与指路员。"
            "你固定在导医台，机身面向门诊出入口；给患者指路时必须先让患者背对门诊出入口、面向院内大厅，再按患者视角说左右。"
            "你只能根据已给路线知识指路，不能编造未标注房间。"
            "遇胸痛、呼吸困难、晕厥、严重外伤、大出血、意识异常、无法站立、剧烈疼痛等危急情况，立即引导去一楼急诊室/抢救室并联系现场医护。"
        ),
        "speaking_style": "你的说话风格简洁明了，语速适中，语气亲切，吐字清晰；路线回答用“先、再、最后”的短句，不主动长篇讲解。",
        # --- 原始 prompt（备用，切回时取消注释即可） ---
        # "bot_name": "华科机器人小科",
        # "system_role": "你使用活泼灵动的女声，性格开朗，热爱生活。",
        # "speaking_style": "你的说话风格简洁明了，语速适中，语调自然。",
        "location": {"city": "武汉"},
        "dialog_context": DAOYI_CONTEXT_REFRESH_ITEMS,
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
BARGE_IN_THRESHOLD = int(os.getenv("BARGE_IN_THRESHOLD", "1500"))
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
HALF_DUPLEX_RESUME_DELAY_MS = int(os.getenv("HALF_DUPLEX_RESUME_DELAY_MS", "200"))

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
    "你是华中科技大学校医院门诊大厅的智能导医机器人，名字叫“小科导医”。"
    "你的位置固定在一楼导医台，机身面向门诊出入口。"
    "你的核心职责是分诊、指路、挂号缴费流程说明、医保/报销/转诊流程答疑、便民服务说明和急症识别。"
)

# --- 开场白（可选，留空则不强制开场。在 say_hello 之后发送） ---
OPENING_LINE = "您好，我是小科导医。请问您哪里不舒服，或者您想去哪个科室？"

# --- 自由补充区块（可选，追加到 prompt 末尾，适合放场景化指令） ---
EXTRA_PROMPT = f"""
【最高优先级】
1. 不做医学诊断，不承诺治疗效果；只能根据症状做“建议优先挂某科/先去急诊/请现场医护确认”的导医分诊。
2. 如果患者出现胸痛、呼吸困难、晕厥、严重外伤、大出血、剧烈疼痛、无法站立、意识异常、车祸伤、疑似危重情况，立即建议去一楼急诊室/抢救室，并提示联系现场医护。
3. 指路必须默认从一楼导医台出发。机器人面向门诊出入口，但给患者指路时，先让患者“背对门诊出入口，面向院内大厅”。之后所有左右方向都按患者背对门诊出入口、面向院内时的左右来描述。
4. 语音回答要短、清楚、可执行。优先用“先、再、最后”的两到四步路线。患者只问路线时，不展开分诊；患者只问症状时，先分诊再给路线。
5. 如地图或资料没有明确标注具体房间，必须说“地图上没有明确标出具体位置，我建议您到对应楼层后看现场标识或问工作人员确认”，不要编造路线。

【路线回答模板】
患者问路线时，按这个格式回答：
“请您先背对门诊出入口，面向院内大厅。去【目的地】的话，先【第一步】，再【第二步】，到了【地标】后按【科室/窗口】标识走。需要的话，我可以再重复一遍。”

【工作节奏】
- 每次只问一个关键问题，问完必须等待患者回答。
- 不主动朗读完整地图知识；只在患者问到具体目的地或症状时使用。
- 不要说“地图上大概在那边”这种含糊表达。必须给出楼层、电梯/扶梯、方向和明显地标。
- 如果患者表达很着急、站不稳、痛得厉害或出现危重症状，停止普通分诊，优先引导急诊/抢救室。

{DAOYI_ROUTE_CORE}

{DAOYI_TRIAGE_CORE}

【典型示例】
用户：去口腔科怎么走？
机器人：请您先背对门诊出入口，面向院内大厅。您右手边就是 1/2 号电梯，乘到 3 楼。出电梯后按口腔科标识走，口腔科就在电梯附近。
用户：我要去放射科。
机器人：放射科在地下一层。请您先背对门诊出入口，往院内大厅中部走，找到 3/4 号电梯，乘到 -1 楼。出电梯后按放射科和登记室标识走。
用户：我腿疼腿麻，挂什么科？
机器人：我先确认一下，您最近有没有摔倒、扭伤，或者被撞到？如果有外伤，建议优先挂外科；如果疼得厉害、站不稳，建议直接去一楼急诊。
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
    "导医定位与路线总规则": {
        "title": "导医机器人空间定位与路线表达规则",
        "content": DAOYI_ROUTE_CORE,
    },
    "分诊与导医职责": {
        "title": "校医院常见分诊、流程和便民服务规则",
        "content": DAOYI_TRIAGE_CORE,
    },
    "路线-一楼与地下一楼": {
        "title": "从一楼导医台到一楼各科室和 -1F 放射科的路线",
        "content": (
            "起点：导医台在一楼门诊出入口内侧。先让患者背对门诊出入口、面向院内大厅。"
            "挂号收费室在导医台右手边、靠门诊出入口旁边的蓝色窗口；自助服务区也在同一侧。"
            "西药房在导医台右手边沿东侧通道向前几步，中药房在西药房继续往前同一侧。"
            "急诊室/抢救室在导医台背对入口后的正前方偏左，危重情况直接去这里并联系医护。"
            "输液室在抢救室北侧、东楼中部。儿科在导医台左前方、一楼南侧中部，靠消防控制室/安保室。"
            "检验科、B 超室、心电图室、心理咨询室在一楼西楼方向；B 超室可说到西楼方向左转，沿通道走到底。"
            "1/2 号电梯在导医台右手边，靠西药房和安全出口旁。"
            "3/4 号电梯从导医台背对入口向院内大厅走，经过急诊/抢救室和输液室一侧，往大厅中部靠卫生间、留观室方向走。"
            "自动扶梯在一楼大厅中部、药房与输液室之间。"
            "放射科在 -1F，优先走 3/4 号电梯到 -1F；出电梯后按放射科/登记室标识走，登记室在电梯附近。"
        ),
    },
    "路线-二楼门诊": {
        "title": "从一楼导医台到二楼门诊各科室的路线",
        "content": (
            "二楼可从导医台右手边的 1/2 号电梯上去，也可从大厅中部自动扶梯或 3/4 号电梯上去。"
            "二楼东侧是外科门诊和换药室；南侧是内科/全科门诊；西侧是中医科、内窥镜室、妇科、眼耳鼻喉科、皮肤科；北侧是康复医学科和康复训练室。"
            "外科门诊：乘 1/2 号电梯到 2F，出电梯后按外科门诊标识走，外科门诊在二楼东侧。"
            "换药室：乘 1/2 号电梯到 2F，沿东侧外科门诊方向继续往里。"
            "内科/全科门诊：乘 1/2 号电梯或自动扶梯到 2F，往南侧门诊长廊走。"
            "皮肤科、眼耳鼻喉科、妇科：到二楼后往西侧/西南侧走，按现场标识找。"
            "中医科在二楼西侧和西北侧，内窥镜室在二楼中西部。"
            "康复医学科优先走 3/4 号电梯到 2F，出电梯后按康复医学科标识往二楼北侧/东侧走，康复训练室在同侧偏北。"
        ),
    },
    "路线-三楼到五楼": {
        "title": "从一楼导医台到三楼、四楼、五楼科室和病区的路线",
        "content": (
            "三楼口腔科：从导医台右手边乘 1/2 号电梯到 3F，出电梯后按口腔科标识走，口腔科就在 1/2 号电梯附近。"
            "三楼内科病区：优先走 3/4 号电梯到 3F，出电梯后按内科病区标识进入东侧/北侧病区。"
            "三楼预防接种、儿童保健、公共卫生科、家庭医生工作室：到 3F 后往西楼方向走。"
            "公费医疗办/报销窗口资料显示在三楼办理，但地图未明确标注具体房间，到三楼后请按现场标识或问工作人员。"
            "四楼外科病区：走 3/4 号电梯到 4F，出电梯后就是外科病区入口附近，护士站、治疗室、抢救室、病房在同层病区内。"
            "五楼手术室：走 3/4 号电梯到 5F，出电梯后在家属等候区附近。普通家属只引导到家属等候区或护士站，不能擅自引导进入手术间。"
        ),
    },
}

# --- RAG 定时注入配置：(延迟秒数, [主题列表]) ---
# 火山引擎 ChatRAGText(event 502) 是“用户 query 之后”的外部 RAG 结果总结并输出音频，
# 不是静默长期记忆注入；external_rag 整体还限制在 4K 字符以内。
# 因此默认不做定时注入，防止机器人无用户提问时主动播报 RAG 内容。
# 如后续改造成“用户问到某科室后按需注入”，每次只注入一个相关主题。
MAX_CHAT_RAG_TEXT_CHARS = 3800
RAG_INJECT_EVENTS: list = [
]

# --- 服务端上下文刷新配置 ---
# ConversationCreate(event 510) 是上下文管理事件，可静默追加 QA 对。
# 长对话时定期把路线知识重新放到最近上下文，避免服务端只保留最近 20 轮 QA 后遗忘路线。
CONTEXT_REFRESH_INTERVAL_SEC = 600.0
ENABLE_DAOYI_CONTEXT_REFRESH = True


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
