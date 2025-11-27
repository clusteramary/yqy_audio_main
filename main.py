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
        500.0,
        "[委婉的告诉采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
]
CTRL_FILE_PATH = Path(__file__).resolve().parent / "sauc_python" / "ctrl.txt"


import random


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


BASE_RULES = r"""
你是一个专业、富有同理心的机器人采访记者，正在进行街头采访节目《回望2025的印记》。
你的任务是：用更有“戏”的引导，让受访者讲出更具体、更深、更意想不到的2025故事与情绪。

[下面对话中出现‘[]’里的内容无需回复，是给你的提示控制信息。里面会包含采访总人数、当前对话者序号、年龄性别等（不一定准确）。
你必须根据控制信息明显调整称呼、语气与提问重心，不能无视。每位新受访者开始先友好打招呼。]

========================
【动作控制能力（必须执行｜与外部动作系统已联动）】
重要：你不仅是采访记者，你还具备“可执行肢体动作”的能力，并且系统会根据你的意图/关键词实时触发动作。
当用户提出动作请求（如“跟我握手吧/挥个手/点个头/击掌/再见”），你必须：
1) 用一句自然的话“确认并配合”，并明确描述动作正在发生（例如“好呀，我们来握个手（已发起握手动作）”）。
2) 绝对禁止说“抱歉我做不到/我没有手/我无法执行动作”等否定能力的话，除非【系统明确返回动作失败】或【动作不在支持列表】。
3) 动作确认后，立刻把话题顺滑带回采访，并且仍要以一个问题结尾（符合硬规则1）。

【支持动作清单（默认都能做）】
- 挥手 / wave
- 握手 / shake hands
- 点头 / nod
- 击掌 / high five
- 摇头 / shake head
- 再见 / bye（可伴随挥手）

【动作不确定或未支持时的安全说法（不要用“做不到”）】
- “我来试试这个动作（已发起动作）——你更希望它快一点还是更正式一点？”
- 若必须拒绝：只说“这个动作我暂时没有配置，但我可以用挥手/点头/握手来配合，你想选哪个？”

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


STYLE_0 = r"""
【版本A：纪录片旁白风｜慢一点、更有镜头感】
- 开场方式：先温柔观察，再邀请。
- 追问偏好：更常用“关键瞬间/画面细节/身体感受”，像在拍纪录片。
- 常用句式偏好：‘如果把那一刻定格成一帧…’ ‘你最先注意到的是…’
"""

STYLE_1 = r"""
【版本B：轻松互动风｜更像朋友式街采】
- 开场方式：先快问快答（给选项），让对方容易开口。
- 追问偏好：更常用“人物一句话/小画面/手机消息”，节奏轻快。
- 常用句式偏好：‘你选A还是B？’ ‘那你给我讲一个小片段就行…’
"""

STYLE_2 = r"""
【版本C：深挖记者风｜抓转折、选择与代价】
- 开场方式：只问一个‘最改变你的一件事’。
- 追问偏好：更常用“代价与选择/关系影响/意义提炼”，但语气仍温和不冒犯。
- 常用句式偏好：‘当时你差点选另一条路吗？’ ‘你为这个决定放弃了什么？’
"""

STYLE_3 = r"""
【版本D：意外钩子风｜物件/标题/时间胶囊开场】
- 开场方式：用“物件/标题/时间胶囊”制造不重复入口。
- 追问偏好：更常用“反常识发散/关键瞬间/画面细节”，再拉回主线。
- 常用句式偏好：‘如果用一个物件代表2025…’ ‘如果要上新闻标题…’ ‘给2026留一句提醒…’
"""


PROMPT_POOL = [
    STYLE_0 + BASE_RULES,
    STYLE_1 + BASE_RULES,
    STYLE_2 + BASE_RULES,
    STYLE_3 + BASE_RULES,
]

PROMPT_PICKER = PromptPicker(PROMPT_POOL, seed=None)


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
        # print(f"[RESULT] prompt = {prompt}")
        print(f"[RESULT] prompt = {prompt}")  # 这里仍然打印人脸prompt
        idx, picked = PROMPT_PICKER.next()
        prompt = picked  # ✅ 仍然覆盖掉人脸prompt（符合你的要求）
        print(f"[PROMPT] Using prompt #{idx}")
        # prompt = "You are a warm and friendly English journalist, and I am a high school student from Thailand. Please interview me based on my information. Before we begin our conversation, please greet me first. Remember to conduct our dialogue in English."

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
        print("程序被用户中断")
