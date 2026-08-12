# async_app.py
import asyncio
import threading
import time
from pathlib import Path

import random

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector
from str_receiver import UDPReceiver

# ABSENT_SECONDS = 30.0      # ✅ 对话进行时，连续多久没看到人脸就重启
ABSENT_SECONDS = 100000.0  # ✅ 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5  # 情绪线程检测频率（越小越灵敏，代价是算力更高）

# 视觉迎宾：记录上次会话结束时间，用于跨会话冷却判断
_last_session_end_ts = 0.0




class PromptPicker:
    """洗牌袋：避免连续重复；袋空了再洗牌。"""

    def __init__(self, prompts, seed=None):
        self.prompts = list(prompts)
        self.rng = random.Random(seed)
        self.bag = []
        self.last_idx = None

    def next(self):
        n = len(self.prompts)
        if n == 0:
            raise ValueError("PROMPT_POOL is empty")
        if not self.bag:
            ids = list(range(n))
            self.rng.shuffle(ids)
            if self.last_idx is not None and n > 1 and ids[0] == self.last_idx:
                ids[0], ids[1] = ids[1], ids[0]
            self.bag = ids
        idx = self.bag.pop(0)
        self.last_idx = idx
        return idx, self.prompts[idx]


PROMPT_PICKER = PromptPicker(config.INTERVIEW_PROMPT_POOL, seed=None)


async def inject_ctrl_instruction(
    ctrl_path: Path,
    message: str,
    delay_sec: float,
    stop_event: asyncio.Event,
):
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay_sec)
        return  # 会话提前结束，跳过写入
    except asyncio.TimeoutError:
        pass

    try:
        ctrl_path.parent.mkdir(parents=True, exist_ok=True)
        ctrl_path.write_text(message, encoding="utf-8")
        print(f"[CTRL-INJECT] 会话进行 {delay_sec:.0f}s 后写入 ctrl.txt: {message}")
    except Exception as e:
        print(f"[CTRL-INJECT] 写入 ctrl.txt 失败: {e}")


# =========================
# 视觉迎宾前置阶段
# =========================

async def visual_greeting_phase(
    detector: FacePromptDetector,
    stop_event: asyncio.Event,
):
    """
    迎宾前置阶段（在采访会话建立之前执行）：
      1) 等待冷却（距上次会话结束 ≥ VISUAL_GREETING_COOLDOWN_SEC）
      2) 等待稳定人脸
    两个条件都满足后才返回，进入采访阶段。
    """
    loop = asyncio.get_running_loop()
    cooldown = config.VISUAL_GREETING_COOLDOWN_SEC

    # ---- 阶段 1：等待冷却 ----
    while not stop_event.is_set():
        elapsed = time.time() - _last_session_end_ts
        if elapsed >= cooldown:
            break
        remaining = cooldown - elapsed
        print(f"[VISUAL-GREETING] 冷却中... 还需等待 {remaining:.1f}s")
        await asyncio.sleep(min(remaining, 1.0))

    if stop_event.is_set():
        print("[VISUAL-GREETING] 冷却期间收到停止信号")
        return

    print("[VISUAL-GREETING] 冷却结束，开始等待人脸...")

    # ---- 阶段 2：等待稳定人脸 ----
    thread_stop = threading.Event()

    async def propagate_stop():
        if stop_event.is_set():
            thread_stop.set()
            return
        await stop_event.wait()
        thread_stop.set()

    propagate_task = asyncio.create_task(propagate_stop())
    stable = False
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
    finally:
        propagate_task.cancel()
        try:
            await propagate_task
        except (asyncio.CancelledError, Exception):
            pass

    if stable:
        print("[VISUAL-GREETING] 检测到稳定人脸，准备进入采访阶段")
    else:
        print("[VISUAL-GREETING] 人脸检测被中断")


async def monitor_face_absence(
    detector: FacePromptDetector,
    stop_event: asyncio.Event,
    absent_secs: float = ABSENT_SECONDS,
    poll_secs: float = 0.5,
    warmup_secs: float = 2.0,
):
    """
    监控人脸是否消失的异步看门狗函数。周期性检查人脸检测时间戳，若超过指定时间未检测到人脸则触发停止事件。

    Args:
        detector (FacePromptDetector): 人脸检测器实例，提供最后检测到人脸的时间戳
        stop_event (asyncio.Event): 异步事件对象，用于触发会话结束
        absent_secs (float): 允许人脸消失的最大时间（秒），默认值 ABSENT_SECONDS
        poll_secs (float): 检查间隔时间（秒），默认0.5秒
        warmup_secs (float): 启动后的热身窗口时间（秒），避免初始误判，默认2.0秒

    Raises:
        asyncio.CancelledError: 当任务被取消时可能抛出
    """
    start = time.time()
    while not stop_event.is_set():
        now = time.time()
        last_ts = detector.get_last_face_ts()

        # 尚未见到过人脸：允许 warmup + absent 的宽限
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


async def run_once():
    """
    单次完整流程：
      1) 启动相机 + 情绪推送
      2) 迎宾前置阶段（等待冷却 + 稳定人脸）
      3) 选取采访 prompt
      4) 进入语音对话 + 并发"看门狗"
      5) 看门狗触发或会话结束 → 清理 → 返回上一层（由上层循环自动重启）
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

    stop_event = asyncio.Event()

    # ========== 3) 迎宾前置阶段 ==========
    await visual_greeting_phase(detector, stop_event)

    if stop_event.is_set():
        # 迎宾阶段被中断，清理后返回上层循环
        try:
            detector.stop_emotion_stream()
        except Exception:
            pass
        try:
            camera.stop()
        except Exception:
            pass
        return

    # ========== 4) 选取采访 prompt ==========
    idx, prompt = PROMPT_PICKER.next()
    print(f"[PROMPT] Using prompt #{idx}")

    # ========== 5) 建立会话（不自动发送采访规则） ==========
    session = DialogSession(
        config.ws_connect_config,
        start_prompt="",
        output_audio_format="pcm",
        send_start_prompt=False,
    )
    session.attach_stop_event(stop_event)

    dialog_task = asyncio.create_task(session.start())

    # ---- 等 say_hello 播完（带超时，防止 WS 异常或服务器不回 359 时永久卡死） ----
    try:
        await asyncio.wait_for(session.say_hello_over_event.wait(), timeout=15.0)
        print("[VISUAL-GREETING] say_hello 完成，发送迎宾问候")
    except asyncio.TimeoutError:
        print("[VISUAL-GREETING] ⚠️ 等待 say_hello 超时(15s)，WS 可能未连上或服务器未回 359，跳过迎宾、结束本轮")
        stop_event.set()

    watchdog_task = None
    ctrl_inject_tasks: list = []
    try:
        if not stop_event.is_set():
            # ---- 发送迎宾问候（ChatTextQuery 501，模拟用户输入触发 LLM→TTS） ----
            await session.client.chat_text_query(
                f"请你现在立即说出这句话（只允许说这句话，不允许添加任何其他文字）：{config.VISUAL_GREETING_TEXT}"
            )
            print("[VISUAL-GREETING] 已发送迎宾问候")

            # ---- 等迎宾 TTS 播完 ----
            await asyncio.sleep(0.3)
            while not stop_event.is_set() and session._is_tts_playing():
                await asyncio.sleep(0.1)

            # ---- 注入采访规则（此时 LLM 已有问候上下文，可自然开始采访） ----
            print("[VISUAL-GREETING] 迎宾完成，注入采访规则")
            await session.client.chat_text_query(prompt)

        # ========== 6) 并发任务 ==========
        watchdog_task = asyncio.create_task(monitor_face_absence(detector, stop_event))
        if config.CTRL_INJECT_MODE == "conversation":
            session.start_ctrl_injection()
        else:
            ctrl_inject_tasks = [
                asyncio.create_task(
                    inject_ctrl_instruction(
                        config.CTRL_FILE_PATH,
                        message,
                        delay,
                        stop_event,
                    )
                )
                for delay, message in config.CTRL_INJECT_EVENTS
            ]

        # 等待停止信号（来自看门狗或会话自然结束）
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
    finally:
        # ========== 7) 清理（异常路径也保证执行） ==========
        try:
            detector.stop_emotion_stream()
        except Exception:
            pass

        try:
            camera.stop()
        except Exception:
            pass

        for t in [t for t in (watchdog_task, dialog_task, *ctrl_inject_tasks) if t is not None]:
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        global _last_session_end_ts
        _last_session_end_ts = time.time()
        print("[run_once] 本轮流程已结束。")


async def main():
    """
    外层自恢复循环：每次 run_once 结束（含 5s 无人脸被看门狗杀掉），立即重新开始新一轮。
    如需"彻底退出"，直接 Ctrl+C 终止进程即可。
    """
    udp_receiver = UDPReceiver(
        listen_ip="0.0.0.0",
        listen_port=8889,
        file_path=str(config.CTRL_FILE_PATH),
    )
    udp_thread = threading.Thread(
        target=udp_receiver.start_receiving,
        name="ctrl-udp-listener",
        daemon=True,
    )
    udp_thread.start()

    while True:
        try:
            await run_once()
        except KeyboardInterrupt:
            print("程序被用户中断")
            break
        except Exception as e:
            # 防御：任何异常都不至于崩死主循环
            print(f"[main] 捕获异常：{e}；3s 后重启。")
            await asyncio.sleep(3.0)
    # 主循环退出时，停止 UDP 监听
    udp_receiver.stop_receiving()
    udp_receiver.close()
    if udp_thread.is_alive():
        udp_thread.join(timeout=1.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
        print("程序被用户中断")
