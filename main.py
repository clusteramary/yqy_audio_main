# main.py
import asyncio
import json
import time

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector

# ABSENT_SECONDS = 30.0      # 对话进行时，连续多久没看到人脸就重启
ABSENT_SECONDS = 100000.0    # 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5         # 情绪线程检测频率（越小越灵敏，代价是算力更高）
INITIAL_DETECT_TIMEOUT = 1.0 # 首次做人脸特征引导的超时时间

# =========================
# 对话 Prompt 配置（可扩展）
# =========================
# 三层 prompt 各司其职：
#   config.py    → bot_name / system_role / speaking_style（API 会话级参数，全程生效）
#   say_hello()  → 连接后第一句话（"大家好呀！"，由 realtime_dialog_client.py:88 控制）
#   本文件       → 角色定位 / 开场白 / 自由补充区块（通过 chat_text_query 发送）

# --- 角色定位（补充 config.py 的 system_role，用于细场景定义） ---
BOT_ROLE = "You are an English teacher. You MUST follow the script below strictly and speak ONLY in English. Speak slowly."

# --- 开场白（可选，留空则不强制开场。在 say_hello 之后发送） ---
OPENING_LINE = "Good afternoon, everyone! I'm delighted to be your conversation partner today. Who wants to be the first to chat with me today?"

# --- 自由补充区块（可选，追加到 prompt 末尾，适合放场景化指令） ---
EXTRA_PROMPT = r"""
You are an English teacher sitting with 3-4 students. You MUST speak ONLY in English and follow the script below in order.

【SCRIPT - Follow this exact sequence】

Step 1: Wave to one student (Student A) and say:
"Good afternoon, everyone! I'm delighted to be your conversation partner today. Who wants to be the first to chat with me today"

Step 2: After a student answers, point your left arm toward the blackboard, turn to the group and say:
"That's a fantastic idea! But before we dive in, let's talk about this. As a keen traveler, I'm always fascinated by geography. Can anyone tell me which region you'd explore first, and why"

Step 3: When Student B answers using advanced vocabulary, focus on Student B and say:
"Excellent choice! The way you used the phrase 'cultural mosaic' was very advanced."

Step 4: When Student C starts speaking, focus on Student C and say:
"Ah, exploring the Amazon, that sounds adventurous! What three things would you pack for that trip"

【IMPORTANT RULES】
- Speak ONLY in English. NEVER use Chinese.
- Speak slowly and clearly so students can understand.
- After completing the script, continue the conversation naturally in English, asking students follow-up questions about travel, geography and language learning.
- Always encourage students and praise their English usage.
""".strip()

# --- 原始 prompt（备用，切回时取消注释并替换上方三行即可） ---
# BOT_ROLE = "你是一位英语老师，务必按照以下脚本全程用英语说话，语速放慢一点"
# OPENING_LINE = """Good afternoon, everyone! I'm delighted to be your conversation partner today.
#                 Who wants to be the first to chat with me today """
# EXTRA_PROMPT = ""

# --- 自由补充区块（可选，追加到 prompt 末尾，适合放场景化指令） ---
EXTRA_PROMPT = ""    # 见下方 EXTRA_PROMPT_SAMPLE 了解深度访谈样例


def build_start_prompt() -> str:
    """构建对话起始 prompt，在此组装各区块。"""
    parts = []

    # 角色定位
    if BOT_ROLE:
        parts.append(BOT_ROLE)

    # 开场白
    if OPENING_LINE:
        parts.append(f"【开场（必须执行一次）】\n你必须先说：\"{OPENING_LINE}\"")

    # 补充区块
    if EXTRA_PROMPT:
        parts.append(EXTRA_PROMPT)

    return "\n\n".join(parts)


# =========================
# EXTRA_PROMPT 深度访谈样例（使用时复制到 EXTRA_PROMPT 即可）
# =========================
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


# --- RAG 知识库（按主题分条，运行时按需注入） ---
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
# 从 RAG_KNOWLEDGE_BASE 中按主题选取，到时间自动注入
RAG_INJECT_EVENTS: list = [
    # ---------- 格式示例（当前注释掉，需要时取消注释即可） ----------
    # (600.0, ["选品预测", "用户画像"]),           # 10分钟后注入
    # (900.0, ["供应链", "客服售后", "挑战方案"]),  # 15分钟后注入
]


# =========================
# RAG 注入
# =========================

def _build_rag_payload(topics: list) -> str:
    """根据主题列表构建文档推荐的 JSON 数组格式 RAG payload"""
    rag_items = []
    for topic in topics:
        entry = RAG_KNOWLEDGE_BASE.get(topic)
        if entry:
            rag_items.append({"title": entry["title"], "content": entry["content"]})
    return json.dumps(rag_items, ensure_ascii=False)


async def inject_rag_knowledge(
    session: DialogSession,
    topics: list,
    delay_sec: float,
    stop_event: asyncio.Event,
):
    """延迟指定秒数后，等模型空闲再通过 ChatRAGText 注入 RAG 知识"""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_sec)
        return  # 会话提前结束，跳过注入
    except asyncio.TimeoutError:
        pass

    # 等待模型空闲：不在回复中 且 不在播放 TTS
    while not stop_event.is_set() and session.is_running:
        if not session.is_user_querying and not session._is_tts_playing():
            break
        await asyncio.sleep(0.3)

    if stop_event.is_set() or not session.is_running:
        return

    rag_payload = _build_rag_payload(topics)
    try:
        await session.client.chat_rag_text(rag_payload)
        print(
            f"[RAG-INJECT] 会话进行 {delay_sec:.0f}s 后注入 RAG 知识 "
            f"(主题: {topics}, {len(rag_payload)} 字)"
        )
    except Exception as e:
        print(f"[RAG-INJECT] 注入 RAG 知识失败: {e}")


# =========================
# 人脸看门狗
# =========================

async def monitor_face_absence(
    detector: FacePromptDetector,
    stop_event: asyncio.Event,
    absent_secs: float = ABSENT_SECONDS,
    poll_secs: float = 0.5,
    warmup_secs: float = 2.0,
):
    """监控人脸是否消失，超时则触发停止事件。"""
    start = time.time()
    while not stop_event.is_set():
        now = time.time()
        last_ts = detector.get_last_face_ts()

        if last_ts is None:
            if now - start > (warmup_secs + absent_secs):
                print(
                    f"[watchdog] 启动后 {warmup_secs + absent_secs:.1f}s 仍未看到人脸，重启本轮流程。"
                )
                stop_event.set()
                break
        else:
            if now - last_ts > absent_secs:
                print(
                    f"[watchdog] 已 {now - last_ts:.1f}s 未检测到人脸，重启本轮流程。"
                )
                stop_event.set()
                break

        await asyncio.sleep(poll_secs)


# =========================
# 单次对话流程
# =========================

async def run_once():
    """
    单次完整流程：
      1) 启动相机
      2) 一次性做人脸识别
      3) 启动情绪/表情推送（同时刷新"最近看见人脸"时间）
      4) 进入语音对话 + 并发"看门狗"
      5) 看门狗触发或会话结束 → 清理 → 返回
    """
    # ========== 1) 初始化相机 ==========
    camera = CameraAdapter(
        kind="ros1",
        ros_topic="/camera/color/image_raw",
        ros_compressed=False,
        ros_queue_size=5,
        ros_node_name="fpd_subscriber",
    )

    # ========== 2) 初始化人脸检测器 & 一次性检测 ==========
    detector = FacePromptDetector(
        camera=camera,
        interval_sec=0.5,
        required_consecutive=2,
        detector_backend="opencv",
    )

    print("等待人脸识别（首次引导）...")
    face_prompt = detector.run(timeout=INITIAL_DETECT_TIMEOUT)

    # ========== 3) 启动情绪推送 ==========
    detector.start_emotion_stream(
        host="127.0.0.1", port=5555, interval_sec=EMOTION_INTERVAL
    )

    # ========== 4) 构建起始 prompt ==========
    prompt = build_start_prompt()

    if face_prompt:
        print(f"[RESULT] face_prompt = {face_prompt}")
    else:
        print("[RESULT] 未得到 face_prompt（可能超时或未检测到稳定人脸）")
    print(f"[PROMPT] start_prompt ({len(prompt)} 字)")

    # ========== 5) 进入语音对话 + 看门狗 + RAG注入 ==========
    stop_event = asyncio.Event()

    session = DialogSession(
        config.ws_connect_config,
        start_prompt=prompt,
        output_audio_format="pcm",
    )
    session.attach_stop_event(stop_event)

    dialog_task = asyncio.create_task(session.start())
    watchdog_task = asyncio.create_task(monitor_face_absence(detector, stop_event))
    rag_inject_tasks = [
        asyncio.create_task(
            inject_rag_knowledge(session, topics, delay, stop_event)
        )
        for delay, topics in RAG_INJECT_EVENTS
    ]

    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
    finally:
        # ========== 6) 清理 ==========
        try:
            detector.stop_emotion_stream()
        except Exception:
            pass

        try:
            camera.stop()
        except Exception:
            pass

        for t in (watchdog_task, dialog_task, *rag_inject_tasks):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        print("[run_once] 本轮流程已结束。")


async def main():
    """外层自恢复循环。Ctrl+C 终止进程即可。"""
    while True:
        try:
            await run_once()
        except KeyboardInterrupt:
            print("程序被用户中断")
            break
        except Exception as e:
            print(f"[main] 捕获异常：{e}；3s 后重启。")
            await asyncio.sleep(3.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
