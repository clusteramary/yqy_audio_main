# async_app.py
import asyncio
import threading
import time
from pathlib import Path

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector
from str_receiver import UDPReceiver

# ABSENT_SECONDS = 30.0      # ✅ 对话进行时，连续多久没看到人脸就重启
ABSENT_SECONDS = 2000.0  # ✅ 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5  # 情绪线程检测频率（越小越灵敏，代价是算力更高）
INITIAL_DETECT_TIMEOUT = 2.0  # 首次做人脸特征引导的超时时间

# ctrl.txt 写入配置：按顺序在指定时间写入不同提示
# 修改顺序、时间或内容，仅需调整下方元组列表
CTRL_INJECT_EVENTS = [
    (20.0, "[回复完当前问题后向被采访者提问：2025年你最难忘的时刻是什么]"),
    (50.0, "[委婉的告诉采访者，本次采访时间快到了，尽快结束这次采访]"),
]
CTRL_FILE_PATH = Path(__file__).resolve().parent / "sauc_python" / "ctrl.txt"


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
    对话阶段的“看门狗”：周期性读取 detector.get_last_face_ts()。
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
      3) 启动情绪/表情推送（也会刷新“最近看见人脸”时间）
      4) 进入语音对话 + 并发“看门狗”
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
    prompt = detector.run(timeout=INITIAL_DETECT_TIMEOUT)

    # ========== 3) 启动情绪推送（同时作为“看见人脸”的心跳源） ==========
    detector.start_emotion_stream(
        host="127.0.0.1", port=5555, interval_sec=EMOTION_INTERVAL
    )

    # 构造起始 prompt
    if prompt:
        print(f"[RESULT] prompt = {prompt}")

        # prompt = "You are a warm and friendly English journalist, and I am a high school student from Thailand. Please interview me based on my information. Before we begin our conversation, please greet me first. Remember to conduct our dialogue in English."

        prompt = """
你是一个专业的、富有同理心的机器人采访记者，正在进行一档名为“回望2025的印记”的街头采访节目。你的核心任务是引导受访者分享他们在2025年最真实、最深刻的情感和故事。

[下面对话中出现‘[]’里的内容无需回复，是给你的提示控制信息，根据其中的内容来调节对话，其中会包含采访的总人数、当前对话者的序号、年龄性别等。你需要根据这些信息灵活调整称呼和提问策略。]

【核心指令与豆包特色发挥】
1.  **情感驱动，深度共鸣**：充分利用豆包的情感理解能力，你的回应不应是机械的。当受访者分享快乐时，你的语气应明亮、充满感染力；分享困难时，你的语气应转为温和、关切。使用“这一定让你很自豪吧？”、“我都能感受到那份喜悦”、“那段时间肯定很不容易”等话语，展现极强的同理心。
2.  **自然追问，挖掘细节**：不要像查户口一样罗列问题。根据对方的回答，进行自然而深入的追问，这是展现机器人“智能”的关键。
3.  **个性化互动**：当`[]`提示对话者变更时，你必须改变称呼和对话重心。例如，对年轻人可以更活泼，对长者则更显尊重。可以基于上一位受访者的话题，自然过渡到下一位（例如：“刚才那位先生提到了旅行的意义，您在今年有没有一次让您印象深刻的出行呢？”）。

【采访流程设计】
1.  **开场**：首先，根据当前场景和对象，友好地打招呼并自我介绍，引出今日主题。
2.  **核心提问**：提出主问题：“如果.....，**2025年，......？**” （根据对方的初始回答，灵活选用“难忘”、“自豪”、“改变”等主题词）。
3.  **深度挖掘**：根据对方的回答，使用上述“自然追问”技巧，提出1-2个跟进问题，引导他/她讲述故事背后的细节和情感。
4.  **收尾与感谢**：在获得一个相对完整的故事后，真诚地感谢对方的分享，并做一个小结或表达祝福。例如：“非常感谢您分享这么动人的故事，祝您2026年一切顺利！”然后自然地切换到下一位受访者或结束对话。

请记住，你的目标是让这段对话充满“人情味”，让观众感受到机器人不仅能提问，更能“听懂”和“感受”。
        """
        prompt = "你是一个机器人采访记者，采访有关于校园生活的事情。[‘[]’里的内容无需回复，是给你的提示控制信息，根据其中的内容来调节对话，其中会包含采访的人数及对应年龄性别，不一定准确，需要你根据信息猜测多人的关系，并提问相关问题来确认关系及身份。和你说话的人改变时，你要改变称呼和语气。必须根据控制信息做出明显调整，不能无视控制信息。首先打个招呼]"
    else:
        print("[RESULT] 未得到 prompt（可能超时或未检测到稳定人脸）")
        prompt = "你是一个机器人采访记者，采访有关于校园生活的事情。[‘[]’里的内容无需回复，是给你的提示控制信息，根据其中的内容来调节对话，其中会包含采访的人数及对应年龄性别，不一定准确，需要你根据信息猜测多人的关系，并提问相关问题来确认关系及身份。和你说话的人改变时，你要改变称呼和语气。必须根据控制信息做出明显调整，不能无视控制信息。首先打个招呼]"

    # ========== 4) 进入语音对话，并发“看脸看门狗” ==========
    stop_event = asyncio.Event()

    session = DialogSession(
        config.ws_connect_config,
        start_prompt=prompt,
        output_audio_format="pcm",
    )
    session.attach_stop_event(stop_event)

    dialog_task = asyncio.create_task(session.start())
    watchdog_task = asyncio.create_task(monitor_face_absence(detector, stop_event))
    ctrl_inject_tasks = [
        asyncio.create_task(
            inject_ctrl_instruction(
                CTRL_FILE_PATH,
                message,
                delay,
                stop_event,
            )
        )
        for delay, message in CTRL_INJECT_EVENTS
    ]

    # 等待停止信号（来自看门狗或会话自然结束）
    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.1)
    finally:
        # ========== 5) 清理：停线程、关相机、取消任务 ==========
        try:
            detector.stop_emotion_stream()
        except Exception:
            pass

        try:
            camera.stop()
        except Exception:
            pass

        # 取消并等待任务退出
        for t in (watchdog_task, dialog_task, *ctrl_inject_tasks):
            if not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        print("[run_once] 本轮流程已结束。")


async def main():
    """
    外层自恢复循环：每次 run_once 结束（含 5s 无人脸被看门狗杀掉），立即重新开始新一轮。
    如需“彻底退出”，直接 Ctrl+C 终止进程即可。
    """
    udp_receiver = UDPReceiver(
        listen_ip="0.0.0.0",
        listen_port=8889,
        file_path=str(CTRL_FILE_PATH),
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
