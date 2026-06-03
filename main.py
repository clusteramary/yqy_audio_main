# main.py
import argparse
import asyncio
import json
import threading
import time

import config
from config import DAOYI_CONTEXT_REFRESH_ITEMS
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector

# ABSENT_SECONDS = 30.0      # 对话进行时，连续多久没看到人脸就重启
ABSENT_SECONDS = 100000.0    # 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5         # 情绪线程检测频率（越小越灵敏，代价是算力更高）


def build_start_prompt() -> str:
    """构建对话起始 prompt，在此组装各区块。"""
    parts = []

    # 角色定位
    if config.BOT_ROLE:
        parts.append(config.BOT_ROLE)

    # 开场白
    if config.OPENING_LINE:
        parts.append(f"【开场（必须执行一次）】\n你必须先说：\"{config.OPENING_LINE}\"")

    # 补充区块
    if config.EXTRA_PROMPT:
        parts.append(config.EXTRA_PROMPT)

    return "\n\n".join(parts)


# =========================
# 视觉迎宾（首轮对话冷却后启动）
# =========================

async def visual_greeting(
    detector: FacePromptDetector,
    session: DialogSession,
    stop_event: asyncio.Event,
):
    """
    视觉迎宾循环：
      0) 首先等待首轮对话完成 + 冷却（VISUAL_GREETING_COOLDOWN_SEC 内无用户活动）
      1) 等待人脸 → 发 502 → 等 TTS 播完
      2) 进入冷却 → 冷却结束后回到 1)
    """
    loop = asyncio.get_running_loop()
    cooldown = config.VISUAL_GREETING_COOLDOWN_SEC

    # ---- 首轮冷却：等首轮对话结束后再启动迎宾 ----
    print(f"[VISUAL-GREETING] 等待首轮对话 + 冷却 {cooldown:.0f}s 后启动迎宾监控")
    while not stop_event.is_set():
        elapsed = time.time() - session.last_user_activity_ts
        if elapsed >= cooldown:
            break
        await asyncio.sleep(min(cooldown - elapsed, 1.0))

    if stop_event.is_set():
        return

    print("[VISUAL-GREETING] 冷却结束，开始视觉迎宾监控")

    # ---- 视觉迎宾循环 ----
    while not stop_event.is_set():
        # ---- 等待人脸 ----
        thread_stop = threading.Event()

        async def propagate_stop():
            if stop_event.is_set():
                thread_stop.set()
                return
            await stop_event.wait()
            thread_stop.set()

        propagate_task = asyncio.create_task(propagate_stop())
        try:
            stable = await loop.run_in_executor(
                None,
                lambda: detector.wait_for_stable_face(
                    interval_sec=config.VISUAL_GREETING_INTERVAL_SEC,
                    required_consecutive=config.VISUAL_GREETING_REQUIRED_CONSECUTIVE,
                    min_face_width=config.VISUAL_GREETING_MIN_FACE_WIDTH,
                    stop_event=thread_stop,
                ),
            )
        except Exception as e:
            print(f"[VISUAL-GREETING] 人脸检测异常: {e}")
            stable = False
        finally:
            propagate_task.cancel()
            try:
                await propagate_task
            except (asyncio.CancelledError, Exception):
                pass

        if stop_event.is_set() or not stable:
            break

        # ---- 发送迎宾 502 ----
        greeting_payload = json.dumps(
            [{"title": "迎宾问候", "content": f"请直接回复以下句子：{config.VISUAL_GREETING_TEXT}"}],
            ensure_ascii=False,
        )
        try:
            await session.client.chat_rag_text(greeting_payload)
            print("[VISUAL-GREETING] 已发送迎宾 502")
        except Exception as e:
            print(f"[VISUAL-GREETING] 发送失败: {e}")
            break

        # ---- 等待迎宾 TTS 播完 ----
        while not stop_event.is_set():
            if not session._is_tts_playing():
                await asyncio.sleep(0.3)
                if not session._is_tts_playing():
                    break
            await asyncio.sleep(0.1)

        # ---- 冷却：从播报结束时刻起至少等 cooldown 秒 ----
        greeting_done_ts = time.time()
        print(f"[VISUAL-GREETING] 迎宾播报结束，进入冷却 {cooldown:.0f}s")
        while not stop_event.is_set():
            elapsed = time.time() - greeting_done_ts
            if elapsed >= cooldown:
                break
            await asyncio.sleep(min(cooldown - elapsed, 1.0))


# =========================
# 静默上下文刷新
# =========================

async def refresh_daoyi_context(
    session: DialogSession,
    interval_sec: float,
    stop_event: asyncio.Event,
):
    """周期性用 ConversationCreate 静默刷新导医路线上下文。"""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
            return
        except asyncio.TimeoutError:
            pass

        while not stop_event.is_set() and session.is_running:
            if not session.is_user_querying and not session._is_tts_playing():
                break
            await asyncio.sleep(0.3)

        if stop_event.is_set() or not session.is_running:
            return

        try:
            await session.client.conversation_create(DAOYI_CONTEXT_REFRESH_ITEMS)
            print("[CONTEXT-REFRESH] 已刷新导医路线/分诊上下文")
        except Exception as e:
            print(f"[CONTEXT-REFRESH] 刷新导医上下文失败: {e}")


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
      1) 启动相机 + 情绪推送
      2) 建立 WebSocket 会话（走原始 say_hello + start_prompt 开场）
      3) 并发：视觉迎宾（首轮冷却后启动）/ 看门狗 / 上下文刷新
      4) 看门狗触发或会话结束 → 清理 → 返回
    """
    # ========== 1) 初始化相机 ==========
    camera = CameraAdapter(
        kind="ros1",
        ros_topic="/camera/color/image_raw",
        ros_compressed=False,
        ros_queue_size=5,
        ros_node_name="fpd_subscriber",
    )
    camera.start()

    # ========== 2) 初始化人脸检测器 & 启动情绪推送 ==========
    detector = FacePromptDetector(
        camera=camera,
        interval_sec=0.5,
        required_consecutive=2,
        detector_backend="opencv",
    )
    detector.start_emotion_stream(
        host="127.0.0.1", port=5555, interval_sec=EMOTION_INTERVAL
    )

    # ========== 3) 建立会话（原始开场逻辑） ==========
    prompt = build_start_prompt()
    print(f"[PROMPT] start_prompt ({len(prompt)} chars)")

    stop_event = asyncio.Event()

    session = DialogSession(
        config.ws_connect_config,
        start_prompt=prompt,
        output_audio_format="pcm",
        duplex_mode=getattr(config, "DUPLEX_MODE", "half"),
    )
    session.attach_stop_event(stop_event)

    # ========== 4) 并发任务 ==========
    dialog_task = asyncio.create_task(session.start())
    greeting_task = asyncio.create_task(
        visual_greeting(detector, session, stop_event)
    )
    watchdog_task = asyncio.create_task(monitor_face_absence(detector, stop_event))
    context_refresh_task = None
    if getattr(config, "ENABLE_DAOYI_CONTEXT_REFRESH", False):
        context_refresh_task = asyncio.create_task(
            refresh_daoyi_context(
                session,
                float(getattr(config, "CONTEXT_REFRESH_INTERVAL_SEC", 600.0)),
                stop_event,
            )
        )

    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
    finally:
        # ========== 5) 清理 ==========
        try:
            detector.stop_emotion_stream()
        except Exception:
            pass

        try:
            camera.stop()
        except Exception:
            pass

        tasks_to_cancel = [watchdog_task, dialog_task, greeting_task]
        if context_refresh_task:
            tasks_to_cancel.append(context_refresh_task)

        for t in tasks_to_cancel:
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
