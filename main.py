# async_app.py
import asyncio
import argparse
import os
import random
import threading
import time
from pathlib import Path

import config
from audio_manager import DialogSession

# ---------- 视觉模块（可选依赖）：加载失败时仅关闭视觉功能，语音对话不受影响 ----------
try:
    from CameraAdapter import CameraAdapter
    from FacePromptDetector import FacePromptDetector

    _HAS_VISUAL = True
except Exception as _visual_import_err:  # noqa: E722
    _HAS_VISUAL = False
    CameraAdapter = None
    FacePromptDetector = None
    print(
        f"[VISUAL] 视觉模块加载失败（{_visual_import_err}），"
        "本次运行仅语音对话，迎宾/看门狗功能关闭。"
    )

from str_receiver import UDPReceiver

# ABSENT_SECONDS = 30.0      # 对话进行时，连续多久没看到人脸就重启
ABSENT_SECONDS = 100000.0  # 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5  # 情绪线程检测频率（越小越灵敏，代价是算力更高）
INITIAL_DETECT_TIMEOUT = 1.0  # 首次做人脸特征引导的超时时间


def pick_interview_prompt():
    """根据环境变量或随机选择采访参数。"""
    identity_idx = int(os.getenv("EXPERT_IDENTITY_INDEX", str(random.randint(0, 1))))
    key_side = os.getenv("EXPERT_KEY_SIDE", random.choice(["user_side", "expert_side"]))
    key_idx = int(os.getenv("EXPERT_KEY_INDEX", str(random.randint(0, 1))))

    prompt = config.build_expert_robot_system_prompt(
        identity_index=identity_idx,
        key_side=key_side,
        key_index=key_idx,
    )
    return prompt


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


async def monitor_face_absence(
    detector: "FacePromptDetector",
    stop_event: asyncio.Event,
    absent_secs: float = ABSENT_SECONDS,
    poll_secs: float = 0.5,
    warmup_secs: float = 2.0,
):
    """
    对话阶段的"看门狗"：周期性读取 detector.get_last_face_ts()。
    若超过 absent_secs 没看到人脸，则触发 stop_event 结束本轮会话。
    warmup_secs：容许对话刚开始的热身窗口（避免一开始就误杀）。
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


# =========================
# 视觉迎宾（结束语/静默后启动）
# =========================

async def visual_greeting(
    detector: "FacePromptDetector",
    session: DialogSession,
    stop_event: asyncio.Event,
):
    """
    视觉迎宾循环：
      0) 等待启动条件（开场白播放完毕后，二者之一）：
         a) 结束语已完整说完（文本+服务端TTS合成+下位机播放三条件齐备）
         b) 监听状态麦克风无输入：idle_silence_sec() >= VISUAL_GREETING_SILENCE_SEC
      1) 等待稳定人脸（头部大小达标）→ 说欢迎语（500 直接 TTS，失败退 501）→ 等 TTS 播完
      2) 触发 stop_event 结束本轮会话 → 外层循环重开会话 →
         新会话 say_hello 随机开场白 → 重新进入访谈逻辑。

    整个协程内部任何异常都只打印日志，绝不影响语音对话任务。
    """
    try:
        loop = asyncio.get_running_loop()
        silence_sec = float(getattr(config, "VISUAL_GREETING_SILENCE_SEC", 15.0))

        # ---- 阶段 0：等待启动条件 ----
        print(
            f"[VISUAL-GREETING] 待机：开场白播放完毕（interview_ready）后，"
            f"若「结束语已完整说完」或「监听状态麦克风 {silence_sec:.0f}s 无输入」"
            "则开启迎宾监控"
        )
        while not stop_event.is_set():
            # 等 interview_ready：开场白播完 + 下位机播完 + 尾音延迟 + 麦克风正式恢复。
            # 在此之前（含开场白播放期间）绝不启动静默计时，避免欢迎语与开场白重叠。
            ready_evt = getattr(session, "interview_ready_event", None)
            if ready_evt is not None and not ready_evt.is_set():
                await asyncio.sleep(0.2)
                continue

            # 结束语：三条件齐备才算"说完"（文本 + 服务端TTS合成结束 + 下位机播放结束）
            closing_ready = getattr(session, "closing_spoken_ready", None)
            ending_done = (
                closing_ready()
                if closing_ready is not None
                else (
                    session.is_ending_said()
                    and not session._is_tts_playing()
                    and not session.is_user_querying
                )
            )
            # 静默：只在 LISTENING 状态累计（开场白/机器人回答/欢迎语播放期间恒为 0）
            silent_done = session.idle_silence_sec() >= silence_sec

            if ending_done or silent_done:
                reason = (
                    "结束语已完整说完"
                    if ending_done
                    else f"监听状态麦克风 {silence_sec:.0f}s 无输入"
                )
                print(f"[VISUAL-GREETING] 启动条件满足（{reason}），开始视觉迎宾监控")
                break
            await asyncio.sleep(0.5)

        if stop_event.is_set():
            return

        # ---- 阶段 1：等待稳定人脸 ----
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

        if stop_event.is_set() or not stable:
            return

        # ---- 阶段 2：说欢迎语 ----
        # 首选 500 ChatTTSText 直接 TTS（不经过 LLM，不受采访 prompt 约束，不污染对话上下文）；
        # 发送前等模型空闲 + 用户 ASR 结束；500 失败时退回 501 让模型复读。
        greeting_text = config.VISUAL_GREETING_TEXT
        try:
            while not stop_event.is_set():
                if (not session.is_user_querying) and not getattr(
                    session, "_model_replying", False
                ):
                    break
                await asyncio.sleep(0.1)
            if not stop_event.is_set():
                await session.client.chat_tts_text(False, True, True, greeting_text)
                print("[VISUAL-GREETING] 已发送迎宾欢迎语 500 (ChatTTSText)")
        except Exception as e:
            print(f"[VISUAL-GREETING] 500 欢迎语发送失败: {e}；退回 501 触发模型复读")
            try:
                await session.client.chat_text_query(
                    f"【后台指令，最高优先级，与采访流程无关】请你现在立即原样说出这句话（只允许说这句话，不允许添加任何其他文字）：{greeting_text}"
                )
                print("[VISUAL-GREETING] 已发送迎宾欢迎语 501")
            except Exception as e2:
                print(f"[VISUAL-GREETING] 501 欢迎语发送失败: {e2}")
                return

        # ---- 阶段 3：等欢迎语 TTS 开始播放（最多 5s）再等播完 ----
        # 先等"开始播放"，防止 TTS 合成延迟时误判为已结束而提前重启会话。
        play_start_wait = time.time()
        while not stop_event.is_set():
            if session._is_tts_playing():
                break
            if time.time() - play_start_wait > 5.0:
                print("[VISUAL-GREETING] 5s 内未检测到欢迎语播放，仍继续等待播完")
                break
            await asyncio.sleep(0.1)

        while not stop_event.is_set():
            if not session._is_tts_playing():
                await asyncio.sleep(0.3)
                if not session._is_tts_playing():
                    break
            await asyncio.sleep(0.1)

        if stop_event.is_set():
            return

        # ---- 阶段 4：结束本轮会话 → 外层循环重开会话（say_hello 开场白 → 重新访谈） ----
        print(
            "[VISUAL-GREETING] 欢迎语播报结束，重启会话以重新进入访谈逻辑（说开场白）"
        )
        stop_event.set()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # 视觉迎宾异常绝不影响语音对话
        print(f"[VISUAL-GREETING] 迎宾流程异常（已隔离，不影响对话）: {e}")


async def run_once():
    """
    单次完整流程：
      1) 尝试启动相机与人脸检测（失败仅关闭视觉功能，语音对话不受影响）
      2) 进入语音对话 + 并发"看门狗" + 并发"视觉迎宾"
      3) 迎宾/看门狗/会话结束 → 清理 → 返回上一层（由上层循环自动重启）

    视觉完全故障时的降级路径：detector 为 None → 跳过看门狗与迎宾，
    语言对话照常进行；迎宾任务内部异常也全部隔离。
    """
    # ========== 1) 初始化相机与人脸检测（可选，失败不影响语音对话） ==========
    camera = None
    detector = None
    try:
        if not _HAS_VISUAL:
            raise RuntimeError("视觉模块未加载")

        camera = CameraAdapter(
            kind="ros1",
            ros_topic="/camera/color/image_raw",
            ros_compressed=False,
            ros_queue_size=5,
            ros_node_name="fpd_subscriber",
        )

        detector = FacePromptDetector(
            camera=camera,
            interval_sec=0.5,
            required_consecutive=2,
            detector_backend="opencv",
        )

        print("等待人脸识别（首次引导）...")
        detector.run(timeout=INITIAL_DETECT_TIMEOUT)

        # 启动情绪推送（同时作为"看见人脸"的心跳源）
        detector.start_emotion_stream(
            host="127.0.0.1", port=5555, interval_sec=EMOTION_INTERVAL
        )
        print("[VISUAL] 相机与人脸检测已就绪")
    except Exception as e:
        print(f"[VISUAL] 视觉初始化失败（已隔离，语音对话不受影响）: {e}")
        try:
            if detector is not None:
                detector.stop_emotion_stream()
        except Exception:
            pass
        try:
            if camera is not None:
                camera.stop()
        except Exception:
            pass
        camera = None
        detector = None

    # 构造采访 prompt（根据环境变量或随机选择参数）
    prompt = pick_interview_prompt()
    print("[PROMPT] 使用专家机器人采访 prompt，开场白由 say_hello 随机选择")

    # ========== 2) 进入语音对话，并发看门狗 / 视觉迎宾 ==========
    stop_event = asyncio.Event()

    session = DialogSession(
        config.ws_connect_config,
        start_prompt=prompt,
        output_audio_format="pcm",
        duplex_mode=getattr(config, "DUPLEX_MODE", "half"),
    )
    session.attach_stop_event(stop_event)

    dialog_task = asyncio.create_task(session.start())

    # 看门狗：仅视觉可用时启用（ABSENT_SECONDS 极大时等效关闭）
    watchdog_task = None
    if detector is not None:
        watchdog_task = asyncio.create_task(
            monitor_face_absence(detector, stop_event)
        )

    # 视觉迎宾：总开关（启动参数/config）且视觉可用时启用
    greeting_task = None
    if getattr(config, "ENABLE_VISUAL_GREETING", False) and detector is not None:
        greeting_task = asyncio.create_task(
            visual_greeting(detector, session, stop_event)
        )
        print("[VISUAL-GREETING] 迎宾逻辑已启用（等待结束语/静默条件）")
    else:
        print("[VISUAL-GREETING] 迎宾逻辑未启用")

    # ctrl 定时注入：
    #   conversation 模式（默认）→ ConversationCreate(510) 静默追加对话历史，模型不回复注入文本；
    #   file 模式 → 沿用旧方式，定时写 ctrl.txt，由 dialog_session 绑定下一轮语音后发送。
    if config.CTRL_INJECT_MODE == "conversation":
        session.start_ctrl_injection()
        ctrl_inject_tasks = []
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

    # 等待停止信号（来自迎宾 / 看门狗 / 会话自然结束）
    try:
        while not stop_event.is_set():
            if dialog_task.done():
                if dialog_task.cancelled():
                    break
                error = dialog_task.exception()
                if error is not None:
                    raise error
                break
            await asyncio.sleep(0.1)
    finally:
        # ========== 3) 清理：停线程、关相机、取消任务 ==========
        session.stop()

        try:
            if detector is not None:
                detector.stop_emotion_stream()
        except Exception:
            pass

        try:
            if camera is not None:
                camera.stop()
        except Exception:
            pass

        # 取消并等待任务退出。gather 保证不会因单个任务异常遗漏其他清理。
        tasks = [
            t for t in (watchdog_task, dialog_task, greeting_task, *ctrl_inject_tasks)
            if t is not None
        ]
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

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

    try:
        while True:
            try:
                await run_once()
            except Exception as e:
                # 防御：业务异常不崩溃；asyncio 的取消会自然越过这里进入 finally。
                print(f"[main] 捕获异常：{e}；3s 后重启。")
                await asyncio.sleep(3.0)
    finally:
        udp_receiver.stop_receiving()
        udp_receiver.close()
        if udp_thread.is_alive():
            udp_thread.join(timeout=1.0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="专家机器人语音采访系统")
    parser.add_argument(
        "--duplex-mode",
        choices=("half", "full"),
        default=config.DUPLEX_MODE,
        help="双工模式：half=半双工（默认），full=全双工可打断",
    )
    parser.add_argument(
        "--input-audio-mode",
        choices=("pyaudio", "ros1"),
        default=config.INPUT_AUDIO_MODE,
        help="麦克风输入：pyaudio=本地麦克风，ros1=ROS 音频话题",
    )
    parser.add_argument(
        "--output-audio-mode",
        choices=("pyaudio", "ros1"),
        default=config.OUTPUT_AUDIO_MODE,
        help="扬声器输出：pyaudio=本地扬声器，ros1=下位机 ros_audio_player.py",
    )
    parser.add_argument(
        "--visual-greeting",
        choices=("on", "off"),
        default=None,
        help=(
            "视觉迎宾开关：on=启用，off=禁用；"
            "不传则使用 config.ENABLE_VISUAL_GREETING"
        ),
    )
    args = parser.parse_args()
    config.DUPLEX_MODE = args.duplex_mode
    config.INPUT_AUDIO_MODE = args.input_audio_mode
    config.OUTPUT_AUDIO_MODE = args.output_audio_mode
    config.output_audio_config["mode"] = args.output_audio_mode
    config.output_audio_config["duplex_mode"] = args.duplex_mode
    if args.visual_greeting is not None:
        config.ENABLE_VISUAL_GREETING = args.visual_greeting == "on"
    print(
        f"[启动参数] duplex={args.duplex_mode}, "
        f"input={args.input_audio_mode}, output={args.output_audio_mode}, "
        f"half_resume_delay={config.HALF_DUPLEX_RESUME_DELAY_MS}ms, "
        f"visual_greeting={config.ENABLE_VISUAL_GREETING}"
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
