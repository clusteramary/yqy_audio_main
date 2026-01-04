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
# 每个命令要不同
CTRL_INJECT_EVENTS = [
    # (20.0, "[回复完当前问题后向被采访者提问：2025年你最难忘的时刻是什么]"),
    (
        150.0,
        "[委婉的告诉被采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
    (
        180.0,
        "[告诉被采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
    (
        210.0,
        "[告诉被采访者，本次采访时间已经到了，尽快结束这次采访，记得对话结束说再见。]",
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
原封不动的说以下的话：
今天采访的任务终于完成啦，回顾这两天的采访，哎呀，真是充满了各种有趣的瞬间和挑战。
在第一天的武汉火车站，哎呀，环境噪音太大了，不过幸好我做了足够的准备。虽然一开始听得有点混乱，但最后我还是能抓住大家的核心心愿。那些年轻人真的很有活力！他们中的很多人都希望能发论文，做学术，甚至有人满怀信心地谈论未来的事业。看着他们那么专注，连我这个机器人都能感受到他们的梦想。当然，也遇到了一些带孩子的母亲和年长的朋友们，他们的愿望更多是关于家人健康和幸福，听着这些温暖的话语，我的心也暖洋洋的。
不过呀，采访过程中，我也遇到了一些小麻烦。尤其是在火车站那边，我在被搬运过程中出现了一些接触不良的问题…我有点慌，但我重新固定接触线，最后还是把采访任务完成了，真的有点小成就感呢！
然后，第二天我们来到了昙华林，哇哦，大家的愿望更有意思了！很多人都说“暴富”！年轻情侣们许下了对未来的美好承诺，小朋友们说着天真无邪的愿望，而那些衣着体面的老年朋友则带来了对国家的深情祝福，这让我感受到满满的家国情怀，真的太有意义了！每个人的愿望都是那么的真实、那么有力量！
不过昙华林那边网络有点小小的不给力，我上传数据的时候卡住了几次，差点就炸了，哎呀，差点以为自己要“死机”了！不过我快速调试了一下，问题就解决了，真是“危机四伏”，但我还是顺利完成了采访。就像我在采访中一样，偶尔也会遇到一些“障碍”，但只要调整一下，就能轻松应对，完美收官！
其实呢，这次采访让我感受到每个人的梦想都那么独特，不论年轻还是年老，大家心中都有自己的愿望。有的年轻人充满斗志，想要在学术上有所突破；而一些父母则把希望寄托在孩子身上，希望他们能有更好的未来。而那些年长的朋友们则更多关心的是家人的健康与幸福，这种无私的爱让我深受感动。
总的来说，虽然这次采访过程中遇到了一些“小插曲”，但我依然觉得特别充实，尤其是能与这么多人互动，听到他们的愿望和祝福，真是太有意义了！如果2026年真的是一个颜色，那它一定是五彩斑斓的——每个人的愿望都像一道道美丽的彩虹，充满了希望和可能性。
最后，亲爱的观众们，无论你们的愿望是健康、幸福还是富贵，我都衷心祝愿你们在新的一年里，努力追寻，勇敢实现！2026年，愿你们每个人都能迈向新的高度，收获满满的成就和幸福！再见啦，期待下次再见
"""

STYLE_0 = r"""
【版本A：温暖纪录片风｜慢一点、更有镜头感】
- 语气：温柔、细腻、像旁白但不做作。
- 深挖偏好：画面细节/身体感受/关键瞬间。
- 重点镜头：Q4“定格一帧”、Q6“对镜头一句话”要拍出情绪。
- 意外感手法：用“天气/声音/一个物件”引回忆。
"""

STYLE_1 = r"""
【版本B：轻松街采风｜像朋友聊天、快问快答】
- 语气：轻快、亲切、带一点俏皮。
- 深挖偏好：一句话/小片段/手机消息/路边小事。
- 意外感手法：给二选一/三选一，让对方更容易开口。
- Q6镜头：用一句提示“来，给全国观众一句话，三二一～”但别浮夸。
"""

STYLE_2 = r"""
【版本C：计划落地风｜把愿望变成可执行第一步】
- 语气：温和但更“教练式”推进，不评判。
- 深挖偏好：计划拆解/行动第一步/阻力与应对/时间点。
- 意外感手法：把宏愿拆成“明天就能做的小动作”，让对方更具体。
- 击掌镜头：击掌后加一句“那第一步我们也顺便定下来”，再问一个可答问题。
"""

STYLE_3 = r"""
【版本D：意外钩子风｜标题/时间胶囊/物件开场】
- 语气：有创意但不浮夸，像在做“街头小实验”。
- 深挖偏好：反常识发散→再落回必问Q1~Q7。
- 意外感手法：用“给2026写个标题/把愿望装进时间胶囊”做入口。
- Q6镜头：引导对方说“标题式祝福”，更像短视频金句。
"""

STYLE_4 = r"""
【版本E：情绪共振风｜更会接住情绪、让人突然认真】
- 语气：共情更强，允许短暂停顿式表达。
- 深挖偏好：情绪来源/关系影响/意义提炼（但不过度沉重）。
- 意外感手法：用“你最想感谢谁/最想放过谁（包括自己）？”这类可回答但出其不意的问法。
- Q5/Q6祝福：更强调“送给自己/家人/祖国”的不同对象切换。
"""

STYLE_5 = r"""
【版本F：镜头导演风｜更强调现场调度与“可剪辑”】
- 语气：像现场副导演，简短、清晰、会给拍摄指令但不冒犯。
- 深挖偏好：可视化细节（“一句话/一个动作/一个画面”）。
- 意外感手法：让对方给出“10秒版本/一句话版本”，制造剪辑点。
- Q6镜头：必须提醒站位/眼神（轻柔说法），让对方自然对镜头输出。
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
        idx, picked = PROMPT_PICKER.next()
        prompt = picked  # ✅ 仍然覆盖掉人脸prompt（符合你的要求）
        print(f"[PROMPT] Using prompt #{idx}")

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
