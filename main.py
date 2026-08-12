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
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector
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
    detector: FacePromptDetector,
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


async def run_once():
    """
    单次完整流程：
      1) 启动相机
      2) 一次性做人脸识别并生成初始 prompt
      3) 启动情绪/表情推送（也会刷新"最近看见人脸"时间）
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

    # ========== 2) 初始化人脸检测器 & 一次性检测 ==========
    detector = FacePromptDetector(
        camera=camera,
        interval_sec=0.5,
        required_consecutive=2,
        detector_backend="opencv",
    )

    print("等待人脸识别（首次引导）...")
    detector.run(timeout=INITIAL_DETECT_TIMEOUT)

    # ========== 3) 启动情绪推送（同时作为"看见人脸"的心跳源） ==========
    detector.start_emotion_stream(
        host="127.0.0.1", port=5555, interval_sec=EMOTION_INTERVAL
    )

    # 构造采访 prompt（根据环境变量或随机选择参数）
    prompt = pick_interview_prompt()
    print("[PROMPT] 使用专家机器人采访 prompt，开场白由 say_hello 随机选择")

    # ========== 4) 进入语音对话，并发"看脸看门狗" ==========
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

    # 等待停止信号（来自看门狗或会话自然结束）
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
        # ========== 5) 清理：停线程、关相机、取消任务 ==========
        session.stop()

        try:
            detector.stop_emotion_stream()
        except Exception:
            pass

        try:
            camera.stop()
        except Exception:
            pass

        # 取消并等待任务退出。gather 保证不会因单个任务异常遗漏其他清理。
        tasks = (watchdog_task, dialog_task, *ctrl_inject_tasks)
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
    args = parser.parse_args()
    config.DUPLEX_MODE = args.duplex_mode
    config.INPUT_AUDIO_MODE = args.input_audio_mode
    config.OUTPUT_AUDIO_MODE = args.output_audio_mode
    config.output_audio_config["mode"] = args.output_audio_mode
    config.output_audio_config["duplex_mode"] = args.duplex_mode
    print(
        f"[启动参数] duplex={args.duplex_mode}, "
        f"input={args.input_audio_mode}, output={args.output_audio_mode}, "
        f"half_resume_delay={config.HALF_DUPLEX_RESUME_DELAY_MS}ms"
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
