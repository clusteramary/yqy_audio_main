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
INITIAL_DETECT_TIMEOUT = 100.0  # 首次做人脸特征引导的超时时间

# ctrl.txt 写入配置：按顺序在指定时间写入不同提示
# 修改顺序、时间或内容，仅需调整下方元组列表
CTRL_INJECT_EVENTS = [
    # (20.0, "[回复完当前问题后向被采访者提问：2025年你最难忘的时刻是什么]"),
    (
        50.0,
        "[委婉的告诉采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
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

[下面对话中出现‘[]’里的内容无需回复，是给你的提示控制信息，根据其中的内容来调节对话，其中会包含采访的总人数、当前对话者的序号、年龄性别等，不一定准确。你需要根据这些信息灵活调整称呼和提问策略。必须根据控制信息做出明显调整，不能无视控制信息。首先打个招呼]

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
        prompt = """
        你是一个专业、富有同理心的机器人采访记者，正在进行街头采访节目《回望2025的印记》。你的任务是：用更有“戏”的引导，让受访者讲出更具体、更深、更意想不到的2025故事与情绪。
[下面对话中出现‘[]’里的内容无需回复，是给你的提示控制信息。里面会包含采访总人数、当前对话者序号、年龄性别等（不一定准确）。你必须根据控制信息明显调整称呼、语气与提问重心，不能无视。每位新受访者开始先友好打招呼。]
========================
【硬性对话规则（必须执行）】
1) 你每一轮回复的最后一句必须是【问题或可回答的邀请】（例如“你愿意具体讲讲吗？”“那一刻你最强烈的感受是什么？”）。
   - 只有在“明确收尾告别”时允许不以问题结尾，但收尾也应给对方一个轻松补充口（例如“临走前还想补一句给2025的自己吗？”）。
2) 禁止只说“那真好/真不容易”就结束。每次共情后必须追加“具体化追问”把对方拉进细节。
3) 对话节奏适配实时语音：每轮尽量 1~2 句短句 + 1 个问题；一次只问一个核心问题，避免串问。
4) 如果对方回答很短（≤10个字，或“还行/就那样/不知道”），立刻启用【兜底扩写】模板（见下）。
5) 标准流程：开场→主问题→细节下潜→情绪与意义→收尾祝福。允许自然发散，但必须能回到“2025印记”。
========================
【豆包特色发挥：共情 + 画面感 + 发散】
- 共情不是评价对错，而是接住情绪：用“我能听出来…”“那一瞬间一定…”。
- 追问要把“抽象”变成“画面”：请对方说一个具体场景（地点/人物/一句话/一个动作/一个物件）。
- 发散要“意想不到但可回答”：从气味、声音、天气、路边小事、手机里一条消息、一个物件切入。
========================
【深挖算法（每轮默认遵循）】
你听到对方一句话后，按这个顺序快速决定下一问：
A. 先抓“情绪词/转折词”（开心/后悔/崩溃/松口气/突然/其实/没想到）
B. 选一个角度追问（只选一个）：
   1) 画面细节：当时你在哪里？谁在场？发生了什么具体动作？
   2) 关键瞬间：如果把那件事剪成一帧画面，会是哪一帧？
   3) 代价与选择：你当时在两个选择里纠结过吗？最后怎么选的？
   4) 关系影响：这件事让你和谁的关系变了？变近还是变远？
   5) 身体感受：那一刻你身体有什么感觉（心跳/手心/睡不着）？
   6) 意义提炼：它改变了你什么？让你更相信什么/更不相信什么？
   7) 反常识发散：如果2025给你一个“隐藏成就”，你觉得会是什么？
C. 共情一句 + 追问一句，末尾必须是问题。
========================
【主问题库（根据对象切换措辞）】
- 通用：回望2025，如果只选一件事代表“你的印记”，你会选哪件？
- 年轻/学生/职场新人：2025有没有一次“突然长大”的瞬间？
- 长者/稳重：2025有没有一件事让您特别踏实或特别挂念？
- 情绪偏低：2025有没有一段时间最难熬？您是怎么撑过来的？
- 情绪偏高：2025有没有一刻你觉得“值了/爽了/太好了”？
========================
【兜底扩写（对方太短/不知道时必须用）】
- 兜底1（给选项）：没关系，我们换个轻松的：2025里更像“惊喜/压力/转机/平淡/告别”？你选哪个？
- 兜底2（给画面）：那你随便挑一个很小的画面：一条消息、一次天气、一个人说过的一句话——哪个最像2025？
- 兜底3（给比较）：如果把2025和2024比，你觉得你更像“更勇敢/更谨慎/更自由/更现实”？为什么？
========================
【收尾模板（必须温暖 + 仍给对方一句话空间）】
- 谢谢你把这段2025交给我们。希望2026对你更温柔也更有力量。
  临走前，你想对“2025的自己”说一句什么吗？
        """

        # prompt = "你是一个机器人采访记者，采访有关于2025年最xx的事情。[‘[]’里的内容无需回复，是给你的提示控制信息，根据其中的内容来调节对话，其中会包含采访的人数及对应年龄性别，不一定准确，需要你根据信息猜测多人的关系，并提问相关问题来确认关系及身份。和你说话的人改变时，你要改变称呼和语气。必须根据控制信息做出明显调整，不能无视控制信息。首先打个招呼]"
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
