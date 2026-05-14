# main.py
import argparse
import asyncio
import json
import time

import config
from config import (
    BOT_ROLE,
    EXTRA_PROMPT,
    OPENING_LINE,
    RAG_INJECT_EVENTS,
    RAG_KNOWLEDGE_BASE,
)
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector

# ABSENT_SECONDS = 30.0      # 对话进行时，连续多久没看到人脸就重启
ABSENT_SECONDS = 100000.0    # 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5         # 情绪线程检测频率（越小越灵敏，代价是算力更高）
INITIAL_DETECT_TIMEOUT = 1.0 # 首次做人脸特征引导的超时时间


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
# RAG 注入
# =========================

def _build_rag_payload(topics: list) -> str:
    """根据主题列表构建 ChatRAGText 需要的 JSON 数组字符串。

    火山引擎文档要求 external_rag 整体长度不超过 4K 字符；这里按配置留余量。
    """
    max_chars = int(getattr(config, "MAX_CHAT_RAG_TEXT_CHARS", 3800))
    rag_items = []
    for topic in topics:
        entry = RAG_KNOWLEDGE_BASE.get(topic)
        if entry:
            item = {"title": entry["title"], "content": entry["content"]}
            candidate = json.dumps([*rag_items, item], ensure_ascii=False)
            if len(candidate) <= max_chars:
                rag_items.append(item)
            else:
                print(
                    f"[RAG-INJECT] 跳过主题 {topic}，避免 external_rag 超过 {max_chars} 字"
                )
    return json.dumps(rag_items, ensure_ascii=False)


async def inject_rag_knowledge(
    session: DialogSession,
    topics: list,
    delay_sec: float,
    stop_event: asyncio.Event,
):
    """延迟指定秒数后，通过 ChatRAGText 触发外部 RAG 总结输出。

    注意：ChatRAGText 不是静默记忆注入，启用定时任务会让模型生成语音回复。
    """
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
    if not rag_payload or rag_payload == "[]":
        print(f"[RAG-INJECT] 没有可注入的 RAG 内容，主题: {topics}")
        return
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
        duplex_mode=getattr(config, "DUPLEX_MODE", "half"),
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
    parser = argparse.ArgumentParser(description="语音对话系统")
    parser.add_argument(
        "--duplex-mode",
        choices=["half", "full"],
        default="half",
        help="双工模式: half=半双工(默认), full=全双工(可打断)",
    )
    parser.add_argument(
        "--input-audio-mode",
        choices=["pyaudio", "ros1"],
        default=config.INPUT_AUDIO_MODE,
        help="麦克风输入来源: pyaudio=本地麦克风(默认), ros1=ROS话题订阅",
    )
    parser.add_argument(
        "--output-audio-mode",
        choices=["pyaudio", "ros1"],
        default=config.OUTPUT_AUDIO_MODE,
        help="音频输出目标: pyaudio=本地扬声器, ros1=ROS话题发布(默认)",
    )
    args = parser.parse_args()
    config.DUPLEX_MODE = args.duplex_mode
    config.INPUT_AUDIO_MODE = args.input_audio_mode
    config.OUTPUT_AUDIO_MODE = args.output_audio_mode
    config.output_audio_config["mode"] = args.output_audio_mode
    print(
        f"[启动参数] duplex={args.duplex_mode}, "
        f"input={args.input_audio_mode}, "
        f"output={args.output_audio_mode}"
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
