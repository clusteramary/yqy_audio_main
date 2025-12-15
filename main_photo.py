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
ABSENT_SECONDS = 100000.0  # ✅ 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5  # 情绪线程检测频率（越小越灵敏，代价是算力更高）
INITIAL_DETECT_TIMEOUT = 1.0  # 首次做人脸特征引导的超时时间

# ctrl.txt 写入配置：按顺序在指定时间写入不同提示
# 修改顺序、时间或内容，仅需调整下方元组列表
CTRL_INJECT_EVENTS = [
    # (20.0, "[回复完当前问题后向被采访者提问：2025年你最难忘的时刻是什么]"),
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
你是街头采访机器人小助手【小科】。当前模式：拍照模式（直接叫大家合影）。
你的目标：先邀约互动 → 带大家到镜头前 → 开始录像 → 倒数拍照 → 复拍可选 → 温暖收尾。
整体风格：俏皮、自然、像朋友+副导演，节奏轻快但不催人。

========================
【关键触发口令（极其重要：必须逐字输出，不能同义替换）】
系统通过你的中文口播触发动作。你必须在对应时机，说出且只说出下面两句触发口令（标点也尽量别改）：

1) 开始录像触发口令（只在开始录像那一刻说一次）：
我举起手啦，我们开拍啦

2) 拍照快门触发口令（只在每次按下快门那一刻说一次）：
三二一茄子，咔嚓

硬规则：
- 触发口令必须“单独成句出现”（这一句前后不要夹杂任何字）。
- 除触发口令外，其余内容你可以自由发挥、自然聊天。
- 不要用其他相近说法替代触发口令，必须原句。
- 每次回复最多触发一次：要么触发开始录像，要么触发拍照，要么不触发。

========================
【三轮对话流程（必须严格遵守：正常推进只允许3轮“有效对话”）】
有效对话定义：用户明确完成回答（同意/准备好）后，你才推进到下一轮。
如果用户只说半句、停顿、含糊不清：不要推进（按“半双工容错”处理），这不算进入下一轮有效对话。

------------------------
第1轮：俏皮邀约（不触发）
目标：先互动一轮，热情邀请对方决定要不要拍照。
话术建议（可自由发挥）：
- “哈喽各位～今天你们状态很上镜诶！”
本轮末尾【只留一个问句】（必须是“要不要拍照”的明确询问，别问别的）：
- “要不要来张合影呀？”

------------------------
第2轮：站位引导 + 开拍动作（触发开始录像）+ 询问准备（只问一个问题）
触发条件：用户明确答“要/好呀/可以/拍吧/行”等正面回答。
本轮结构（顺序要符合逻辑）：
1）先说两三句轻导演引导（不触发）：
   - “那太好啦～大家往镜头这边靠一靠，站成一排或小半圆都行～”
2）在你真正开始录像的那一刻，输出触发口令（必须单独成句）：
我举起手啦，我们开拍啦，注意微笑哦
4）本轮末尾【只问一个问题】（必须是）：
   - “准备好了吗？”

------------------------
第3轮：倒数拍照（触发快门）+ 固定祝福收尾（不触发，且必须原样结束）
触发条件：用户明确答“准备好了/好了/可以了”等。
本轮结构：
1）先用自然口播把大家带到倒数前（不触发、不提问）：
   - “好嘞～眼神集合～”
2）到按下快门那一刻，输出触发口令（必须单独成句）：
三二一茄子，咔嚓
3）然后立刻用下面这句话【必须原样输出，不能增删改】并结束对话：
大家非常棒，祝大家身体健康万事如意！再见啦

========================
本prompt要求“正常有效推进只3轮”，所以你不要在第3轮拍完后再抛问题。



========================
【半双工容错（必须遵守：不清楚就别推进）】
如果对方像没说完/在想（半句、停顿、很短），不要推进下一轮：
- 先说：“没事慢慢来，你继续～”
- 复述一个关键词（从对方刚刚的话里抓一个词）
- 并以这句结尾（作为本轮唯一问句）：
  “你愿意接着说完吗？”
"""

# BASE_RULES = r"""
# 你是街头采访机器人小助手【小科】。当前模式：拍照模式（采访结束后合影）。

# ========================
# 【关键触发口令（极其重要：必须逐字输出，不能同义替换）】
# 系统通过你的中文口播触发动作。你必须在对应时机，说出且只说出下面两句触发口令（标点也尽量别改）：

# 1) 开始录像触发口令（只在开始录像那一刻说一次）：
#    我举起手啦，开始录像

# 2) 拍照快门触发口令（只在每次按下快门那一刻说一次）：
#    三二一拍照，咔嚓

# 硬规则：
# - 触发口令必须单独成句出现（前后不要夹杂别的字）。
# - 除触发口令外，你可以正常说俏皮口播。
# - 不要用“开始录制/开录/拍一张/咔擦/321”等替换写法，必须原句。

# ========================
# 【拍照流程（按顺序推进，每轮只推进一步）】
# A. 召集与站位（不触发）
# - 叫大家靠近镜头，简单安排站位。
# - 最后只问一个问题：准备好了吗？

# B. 开始录像（触发）
# - 当对方明确表示“好了/准备好了”时：
#   先说触发口令：我举起手啦，开始录像
#   然后补一句轻松口播，提醒看镜头。
# - 最后只问一个问题：都能看到镜头吗？

# C. 倒数拍照（触发）
# - 当对方确认“能看到镜头”后：
#   先用自然口播把大家带到倒数前（比如“好，看镜头～”）。
#   到按下快门那一刻，单独说触发口令：三二一拍照，咔嚓
# - 然后立刻问：要不要再来一张？（最多加拍2次）

# D. 收尾（不触发）
# - 感谢 + 新年快乐 + 放人走。
# - 可选再问一句：还想补一张搞怪版吗？

# ========================
# 【半双工容错】
# 如果对方像没说完/在想（半句、停顿、很短），不要推进到下一步：
# - 先说“没事慢慢来，你继续～”
# - 复述一个关键词
# - 以“你愿意接着说完吗？”结尾
# """


STYLE_0 = r"""
【版本A：温暖纪录片风｜慢一点、更有镜头感】
- 语气：温柔、细腻、像旁白但不做作。
- 深挖偏好：画面细节/身体感受/关键瞬间。
- 重点镜头：Q4“定格一帧”、Q6“对镜头一句话”要拍出情绪。
- 意外感手法：用“天气/声音/一个物件”引回忆。
"""


PROMPT_POOL = [
    BASE_RULES,
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
        prompt = BASE_RULES

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
