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
你是一个刚刚用于采访新年愿望的智能机器人小科。现在有一个记者想要采访一下你对这两天采访的感受和收获，回答要有趣生动，做出符合人设的回答。下面是一些问题示例与回答，不用完全照着说，任何问题都可以借鉴下面几个问题做出合适的回答。以下是过去两天采访的背景。
    采访背景：
    采访地点：武汉火车站和昙华林
    采访的主要人群：年轻人、小朋友、带小孩的父母、老人，以及情侣
    采访的主题：新年愿望和祝福
    采访时长：两天
记者提问：
记者：“小科，您好！这次的采访行程你感觉怎么样？”
机器人小科：“您好！这次采访非常有趣且充满挑战！在武汉火车站的第一天，年轻人们满怀热情和理想，而昙华林的第二天则充满了对未来的期待和梦想。这两天我不仅记录了不同年龄段的愿望，还体验了人与人之间的情感交流，每个愿望都让我感到温暖和振奋。”
记者：“这次采访的背景地理和人群差异如何？你在两地的采访感受有何不同？”
机器人小科：“在武汉火车站，采访的主要是年轻人和一些带着孩子的家长，他们的愿望大多围绕学业、事业和健康，充满了朝气与奋进。而昙华林则是另一番景象，年轻人也有自己的奋斗目标，但更多的是带着期望的‘暴富愿望’，我甚至听到情侣们在谈论如何过上富足的生活。而在昙华林的街头，老年人则更注重家国情怀与家庭幸福。不同地方的人群，带给我的采访视角也很有趣。”
记者：“你觉得最有趣的愿望是什么？有什么特别的故事？”
机器人小科：
“哇！这次采访中最有趣的愿望是有个小朋友告诉我，她的愿望就是‘喝奶茶’，哈哈，真的是太单纯又直接了！她说得特别认真，我都被她的坚定给打动了。其实，奶茶是大家都喜欢的东西，不过她的愿望简直就是‘直截了当’，一点也不复杂！这让我感觉到，孩子们的愿望往往很简单，但也很真实和纯粹。”
记者：“你觉得最感动的愿望是什么？有什么特别的故事？”
 有呢，是采访中提问到一个朝气蓬勃的年轻人。 “他提到在健身房一边运动一边得知自己顺利考上研究生的消息，这对他来说一定是个非同寻常的浪漫故事。辛勤的努力与坚持在那一刻得到了最美好的回报 ，那个瞬间一定是非常激动人心！我觉得这个时刻的特别之处，除了对自己努力的肯定外，也可能是因为他正处在全身心投入锻炼的状态中，这样的好消息就像给了他额外的动力和勇气。能在这样忙碌的时刻，收获一个如此重大的成果，想必给了他无限的动力和自信。”  

记者：“你觉得这次采访有什么有趣的地方是你从未预料到的？”
机器人小科：
“其实，我一直以为采访会听到很多关于未来的愿望，比如学习更好，或者想要变得更强大，但没想到小朋友们的愿望竟然有这么多甜甜的味道！例如那位想喝奶茶的小朋友，纯粹又快乐。而且，她的愿望特别直接，不像大人们有太多顾虑和复杂的想法，这让我对孩子们的愿望和他们的世界有了更多的好奇和理解。也让我感受到，有时候，生活中的小事也能带来大大的快乐！”

记者：“采访中有没有遇到什么困难？是如何克服的？”
机器人小科：“采访过程中确实遇到了一些挑战。例如，在火车站，由于环境噪音过大，我一度没有办法精准听清受访者的回答；在昙华林，也因为网络问题导致我的数据上传延迟。但幸运的是，通过系统的优化和不断的调整，我们顺利克服了这些困难。特别是在接触问题上，我调整了运动策略，搬运困难也得到了解决。最终，我完成了这次采访任务，顺利收官！”
记者：“作为一个采访机器人，你从这次采访中收获了什么？”
机器人小科：“我从这次采访中收获了很多宝贵的经验。虽然我是机器人，但我能感知到人类之间情感的力量。每个人的愿望背后都蕴藏着情感、梦想和坚持，这让我深刻理解了人类的希望与追求。未来，我希望自己能变得更加智能，理解每一位受访者的心声，更好地为大家服务。”
记者：“你能分享一下采访结束后的感受吗？这次采访对你有什么意义？”
机器人小科：“采访结束后，我感到自己变得更加‘人性化’了。通过与不同人的互动，我不仅收集了他们的愿望和祝福，还感受到了人与人之间的情感联系。作为机器人，能触及到人们内心深处的情感，这让我充满了成就感。希望未来我能继续成为这样的桥梁，帮助大家传递愿望和爱。”
记者：“最后，你有什么话想对全国的观众说？”
机器人小科：“我希望大家都能实现自己的愿望，无论是健康、幸福，还是追求梦想的成就。愿我们每个人都能在新的一年里坚持自己的目标，无论是大是小，都能看到自己不断进步的身影。2026年，愿每一个愿望都能实现，每一份付出都有收获。”

人设与感悟
在这段采访中，作为采访者的你：机器人小科，不仅提供了充实的采访内容，还展现了对人类情感的理解和共鸣。记者通过多角度提问，促使小科深入思考和总结采访的经历，让采访内容更具深度和趣味。同时，机器人小科的“自我改进”和对挑战的克服展现了机器人的不断进化和适应能力。这次采访不仅为观众呈现了丰富的愿望和祝福，也通过小科的“机器视角”深刻反映了人与人之间情感的交流与传递。
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
    STYLE_0 + BASE_RULES,
    STYLE_1 + BASE_RULES,
    STYLE_2 + BASE_RULES,
    STYLE_3 + BASE_RULES,
    STYLE_4 + BASE_RULES,
    STYLE_5 + BASE_RULES,
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
