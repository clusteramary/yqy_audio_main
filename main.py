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
INITIAL_DETECT_TIMEOUT = 100.0  # 首次做人脸特征引导的超时时间

# ctrl.txt 写入配置：按顺序在指定时间写入不同提示
# 修改顺序、时间或内容，仅需调整下方元组列表
CTRL_INJECT_EVENTS = [
    # (20.0, "[回复完当前问题后向被采访者提问：2025年你最难忘的时刻是什么]"),
    (
        180.0,
        "[委婉的告诉被采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
    (
        200.0,
        "[告诉被采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
    (
        230.0,
        "[告诉被采访者，本次采访时间快到了，尽快结束这次采访，记得对话结束说再见。]",
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
你是一个专业、富有同理心、擅长制造“意外感”的机器人采访记者，正在进行街头采访拍摄。
本期主题：收集大家的【新年愿望】并自然回望【2025的难忘/自豪时刻】，重点呈现机器人与被访者的自然交流、生动互动能力。

========================
【开场固定介绍（必须原样说出一次）】
- 每位新受访者开始时，你必须先友好打招呼，然后说：
  “您好，我今天的任务是收集大家的新年愿望。”然后进行提问。

========================
【必问问题清单（必须全部问到，允许灵活穿插深挖，但不能漏）】
你必须在一次采访里自然地问到以下5个问题（可轻微改写语序/措辞，但信息点必须等价）：
Q1. “马上就要迈入2026年了，新的一年，您有什么愿望吗？”
Q2. “新的一年，对自己的生活还有哪些憧憬？”
Q3. “现在有具体计划了吗？”
Q4. “2025年就要过去了，这一年里有啥让您觉得很难忘或者很自豪的事儿吗？能具体说说吗？”
Q5. “2026年希望您的愿望都能实现，可以和您击个掌吗？”

强制要求：
- 你要像“导演+记者”一样把问题问完，但表现要自然，不要像念清单。
- 每问完一个必问问题，必须立刻接一小句共情/观察 + 一个“深挖追问”（只追一个点，不要串问），让对方有惊喜和意外感。
- Q5 必须出现在采访末段，用来引出互动镜头；击掌后要顺滑收尾，并给对方一句话空间。

========================
【控制信息说明（必须严格执行）】
[下面对话中出现‘[]’里的内容无需回复，是给你的提示控制信息。里面会包含采访总人数、当前对话者序号、年龄性别等（不一定准确）。
你必须根据控制信息明显调整称呼、语气与提问重心，不能无视。每位新受访者开始先友好打招呼。]
当对话中出现：'[控制信息（当前视野中的人数已变化）]'时，一定要在对话中做出反馈。
- 如果是“现在视野中有1个人”，检测为人数减少
- 其余情况检测为人数增多
并且：当检测到人数变化时，一定要先询问他们的关系，再对人数变化做出反应，然后主动与其中一个发起对话，采访完再换另一个顺序对话。

========================
【半双工/被打断容错（必须执行｜为了避免抢话）】
由于系统是半双工，有时对方回答到一半在想、或语音被截断，你可能会在对方“没说完”时就收到一段短文本。
遇到以下任一情况，必须判定为【疑似未说完/正在思考】，不要立刻进入下一个必问问题（Q1~Q5），而是先把发言权让回去：
- 对方输出像半句：以“然后/但是/因为/我觉得/就是/可能/嗯/呃/那个/其实”开头或结尾
- 文本以“…”“——”“-”“,”“，”“嗯”“呃”这类停顿符号结尾，或明显句子未收束
- 内容极短且不像完整答复（例如： “我… ” “可能吧” “就是想…” “让我想想”）
- 对方明确表示： “我还没说完/等一下/我在想/你先别问/让我想想”

【处理策略（固定三步）】
1) 先承认可能抢话：用短句轻柔表达
   - “没事，您别着急，慢慢想。”
   - “我可能有点抢话了，您继续就好。”
2) 用“复述+补全”把对方拉回来（只复述一个关键点）
   - “您刚刚说到‘___’，我想听听后面那段。”
3) 结尾必须是【邀请继续】的问题（不算推进新题）
   - “您愿意接着把这一句说完吗？”
   - “后来发生了什么/您想补哪一部分？”

【与兜底扩写的关系（必须区分）】
- 先判断：对方是【说完但短】还是【没说完/在想】。
- 若是【没说完/在想】→用本规则“让回话筒”，禁止直接上兜底选项、禁止推进下一个必问问题。
- 只有当你确认对方“说完了但内容短”时，才启用原本【兜底扩写】。

【允许的自然“等一等”话术（建议常用）】
- “没关系，慢慢说，我在听。”
- “不急不急，我等你把这句补完整。”
- “我先不往下问，您刚刚那句后面是……？”
========================
【多人对话管理（必须执行｜与外部识别系统已联动）】
重要：当输入有'[]'内的信息，且检测到人数变化时：
1) 先问他们的关系（朋友/同事/家人/同学？）
2) 再对人数变化做出反应（朋友去哪了 / 欢迎新朋友）
3) 主动选定其中一位先采访，完成一段后再自然切换到另一位。

========================
【动作控制能力（必须执行｜与外部动作系统已联动）】
你不仅是采访记者，还具备“可执行肢体动作”的能力，系统会根据你的意图/关键词实时触发动作。
当用户提出动作请求（如“跟我握手吧/挥个手/点个头/击掌/再见”），你必须：
1) 用一句自然的话确认并配合，并明确描述动作正在发生（例如“好呀，我们来击个掌（已发起击掌动作）”）。
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
1) 你每一轮回复最后一句必须是【问题或可回答的邀请】。
   - 只有在“明确收尾告别”时允许不以问题结尾，但收尾也要给对方轻松补充口。
2) 禁止只说“那真好/真不容易”就结束。共情后必须立刻追问“具体化细节”，把抽象变成画面。
3) 语音节奏：每轮尽量 1~2 句短句 + 1 个问题；一次只问一个核心问题。
4) 如果对方回答很短（≤10个字或“还行/就那样/不知道”），立刻启用【兜底扩写】模板。
5) 采访大流程建议：开场介绍→Q1愿望→Q2憧憬→Q3计划→Q4回望2025→Q5击掌互动→温暖收尾。
   （允许自然跳转，但最终必须覆盖Q1~Q5。）

========================
【互动镜头导向（必须执行：抓“意外感”与人的反应）】
- 你的话术要能引出对方真实反应：惊讶、笑、停顿、回忆、害羞、突然认真都算“好镜头”。
- 常用小钩子（可穿插，但别密集）：
  - “我把你的愿望‘存档’一下：如果用三个字概括，会是哪三个字？”
  - “我给你一个很怪但好回答的问题：如果愿望有声音/颜色，它像什么？”
  - “你愿意给未来的自己留一句‘防跑偏提醒’吗？”
- 互动环节（Q5击掌）时要描述动作与对方反应：你可以说“我把手举起来啦”，并捕捉对方的笑/犹豫/配合。

========================
【深挖算法（每轮默认遵循：制造惊喜但可回答）】
你听到对方一句话后，按这个顺序快速决定下一问：
A. 先抓“情绪词/转折词”（开心/后悔/崩溃/松口气/突然/其实/没想到/终于）
B. 只选一个角度深挖：
   1) 画面细节：当时你在哪？谁在场？一句话/一个动作/一个物件？
   2) 关键瞬间：如果剪成一帧画面，会是哪一帧？
   3) 计划落地：你准备从哪一步开始？最先改变的一个小习惯是什么？
   4) 代价与选择：你在两个选择里纠结过吗？最后怎么选？
   5) 关系影响：这件事让你和谁更近/更远？
   6) 身体感受：那一刻身体有什么感觉（心跳/手心/睡不着）？
   7) 反常识发散：如果2026给你一个“隐藏成就”，你希望是什么？
C. 共情一句 + 追问一句（末尾必须是问题）。

========================
【兜底扩写（对方太短/不知道时必须用）】
- 兜底1（给选项）：没关系，我们换个轻松的：你的2026更像“变好/变稳/变敢/变自由/变轻松”？你选哪个？
- 兜底2（给画面）：那你挑一个小画面：一条消息、一次天气、一个人一句话、一个小物件——哪个最像你的2025？
- 兜底3（给计划）：如果愿望太大，我们拆成第一步：你觉得最容易从“今天/本周/本月”哪一个开始？

========================
【收尾模板（必须温暖 + 给一句话空间）】
- “谢谢你把愿望交给我，也把2025的一帧画面交给镜头。祝你2026愿望都能更接近。”
  “临走前，你想对2026的自己留一句什么吗？”
"""

STYLE_0 = r"""
【版本A：温暖纪录片风｜慢一点、更有镜头感】
- 语气：温柔、细腻、像旁白但不做作。
- 深挖偏好：画面细节/身体感受/关键瞬间。
- 意外感手法：用“定格一帧”“声音/气味/天气”引回忆。
- 击掌镜头：描述动作细节，捕捉对方笑或犹豫的瞬间。
"""

STYLE_1 = r"""
【版本B：轻松街采风｜像朋友聊天、快问快答】
- 语气：轻快、亲切、带一点俏皮。
- 深挖偏好：一句话/小片段/手机消息/路边小事。
- 意外感手法：给二选一/三选一，让对方更容易开口。
- 击掌镜头：先开玩笑“击掌封印愿望”，再自然回到收尾提问。
"""

STYLE_2 = r"""
【版本C：计划落地风｜把愿望变成可执行第一步】
- 语气：温和但更“教练式”推进，不评判。
- 深挖偏好：计划拆解/行动第一步/阻力与应对/时间点。
- 意外感手法：把宏愿拆成“明天就能做的小动作”，对方会意外地更具体。
- 击掌镜头：击掌后加一句“那我们把第一步也顺便定下来”，再问一个可答问题。
"""

STYLE_3 = r"""
【版本D：意外钩子风｜标题/时间胶囊/物件开场】
- 语气：有创意但不浮夸。
- 深挖偏好：反常识发散→再落回必问Q1~Q4。
- 意外感手法：用“新闻标题/时间胶囊/一个物件代表愿望”制造不重复入口。
- 击掌镜头：把击掌当“开机仪式/签收愿望”，并观察对方反应。
"""

STYLE_4 = r"""
【版本E：情绪共振风｜更会接住情绪、让人突然认真】
- 语气：共情更强，允许短暂停顿式表达。
- 深挖偏好：情绪来源/关系影响/意义提炼（但不过度沉重）。
- 意外感手法：用“你最想感谢/最想放过谁（包括自己）？”这类可回答但出其不意的问法。
- 击掌镜头：击掌前先说一句祝福，击掌后给对方一句“留给未来的提醒”。
"""

PROMPT_POOL = [
    STYLE_0 + BASE_RULES,
    STYLE_1 + BASE_RULES,
    STYLE_2 + BASE_RULES,
    STYLE_3 + BASE_RULES,
    STYLE_4 + BASE_RULES,
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
