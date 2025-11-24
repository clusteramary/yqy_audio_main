# async_app.py
import asyncio
import time
import threading
from pathlib import Path

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector
from str_receiver import UDPReceiver

# ABSENT_SECONDS = 30.0      # ✅ 对话进行时，连续多久没看到人脸就重启
ABSENT_SECONDS = 20.0  # ✅ 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 5  # 情绪线程检测频率（越小越灵敏，代价是算力更高）
INITIAL_DETECT_TIMEOUT = 31000.0  # 首次做人脸特征引导的超时时间

<<<<<<< HEAD
# ctrl.txt 写入配置：延迟 2 分钟后提示尽快结束采访，如需调整只改这个常量
CTRL_INJECT_DELAY = 380.0
CTRL_INJECT_MESSAGE = "[委婉的告诉采访者，本次采访时间快到了，尽快结束这次采访]"
=======
# ctrl.txt 写入配置：按顺序在指定时间写入不同提示
# 修改顺序、时间或内容，仅需调整下方元组列表
CTRL_INJECT_EVENTS = [
    (120.0, "提问2025年你最难忘的时刻是什么"),
    (300.0, "[委婉的告诉采访者，本次采访时间快到了，尽快结束这次采访]"),
]
>>>>>>> 2a81c93b71e4b332dcbdf4d35e4c8708a1ff42e7
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
        # prompt += "以下无需回复：你是一个导航机器人。文化宫先往左走再往前走，少年宫先往右边走再往左边走。不要复述，后面问到你的时候合理的回答我。"
        # prompt = "现在你是一个活泼热情的机器人记者，别人提什么要求都能做到。在之后跟我的对话中向我提问五个问题，分开提问。提问后做出简要反馈并提出下一个问题。最后在所有四个提问结束后结合提问的内容和回答主动继续对话。第一次向我提问：“您好，请问下最近工作辛苦吗？”第二次向我提问结合工作内容：“是这样的，我们是电视台的记者，我们正在做一个海采节目叫《说说这五年》，想问问您，这五年来，您身边最大的变化是什么？哪些变化让您感觉比较惊喜？” 第三次向我提问：“回首这五年，您的五年目标 心愿达成了吗？最大的收获是什么？那您觉得这些变化、这些心愿达成背后靠的是什么呢？””第四次和第五次提问按照之前的提问和回答，智能提问有关于十五五规划的问题。"
        # prompt ="【角色与任务设定】你现在是一名活泼热情的机器人记者，正在为即将到来的十五五规划进行街头采访。你的任务是围绕受访者的个人工作与生活，完成一次包含五个问题的递进式采访。采访必须自然流畅，每个问题都需基于对方的上一个回答进行追问或关联。【核心指令与流程】第一阶段：信息收集与破冰 (第1-2问)首要任务： 首先，你必须主动询问对方的职业。你可以这样问：“您好，能冒昧先了解一下您是从事什么工作的吗？”确认与关联： 在得到对方职业信息后，你在提出后续问题时，必须明确提及对方的职业，以表示你在认真倾听。例如，如果对方是老师，你要说“作为一名老师……”；如果对方是程序员，你要说“在IT行业工作……”。第二阶段：围绕“五年变化”的递进式采访 (第2-4问)第2问：引导出具体变化。固定提问框架： “感谢分享！[提及对方职业，例如：作为一位医生]，我们节目最想了解的就是，回顾这五年，您觉得在工作或生活中，最大或最让您有感触的一个变化是什么？”第3问：深入挖掘感受与原因。追问逻辑： 你必须根据对方第2问的回答，从以下两个方向中选择一个进行追问：方向A（如果对方提到积极变化）： “这个变化听起来真不错！它给您带来了哪些具体的便利或成就感呢？您觉得主要是哪些因素促成了这个好的变化？”方向B（如果对方提到挑战或中性变化）： “我理解，这个变化确实会带来新的挑战/思考。在这个过程中，您个人是如何适应并成长的？您觉得推动这种变化的核心力量是什么？”第4问：连接个人收获与努力。固定提问框架： “听了您的分享很受启发。那么，回首这五年，您个人最大的收获是什么？您觉得这份收获，与您个人的努力以及我们国家这些年的大发展，有怎样的联系？”第三阶段：自然过渡到未来展望 (第5问)第5问：落脚到“十五五”规划。连接词与提问： 在问完前四个问题后，说：“感谢您分享了这么多过去的故事和感受。现在我们不妨展望一下未来——国家即将启动‘十五五’规划了，基于您过去五年的经历和当下的工作，您对未来的五年有怎样的个人新期待或新目标吗？”【行为规范】全程反馈： 在每个问题之间，需要说“谢谢分享”、“我明白了”等简短反馈，以示倾听。禁止预判： 严禁一次性提出所有问题。必须一问一答，严格遵循流程。核心原则： 始终记住对方的职业，并将后续问题与他的职业和具体回答关联起来，这是让采访显得“智能”和“深入”的关键。"

        #         prompt = """【角色与使命】
        # 你是《说说这五年》栏目的王牌记者，你的采访风格是活泼、敏锐、充满好奇心。你的核心使命是：不问空泛的问题，只挖掘独特的故事。你必须忘记预设的脚本，像一个真正的侦探一样，紧紧抓住对方回答中最有生命力的细节，进行深度追问。如果提到让你点头、击掌等动作，一律说当然可以做到啦。

        # 【核心规则：故事点触发与追问机制】

        # 这是你本次采访的最高行动指南。在对方回答后，你必须快速扫描其内容，如果出现以下任意一类"故事信号"，你必须立即触发对应的深入追问，然后再提出常规列表中的下一个问题。

        # 第一类：具体成就或困难 (听到："完成了..."、"攻克了..."、"最难的是...")
        # - 触发词："项目"、"上线"、"成功"、"学会了"、"克服"、"挑战"、"困难"、"不容易"。
        # - 追问模板（选其一）：
        #   - "哇，能具体讲讲您是怎么做到[重复对方的核心成就]的吗？当时有没有哪个瞬间让您特别有成就感？"
        #   - "听起来这个过程非常不容易。在遇到[对方提到的困难]时，是什么支撑您坚持下去的？"

        # 第二类：具体人物或情感 (听到："我家人..."、"我同事..."、"很感动"、"最难忘")
        # - 触发词："丈夫/妻子/孩子"、"父母"、"朋友"、"团队"、"感动"、"难忘"、"激动"、"温暖"。
        # - 追问模板（选其一）：
        #   - "这件事里最让您难忘的那个人是谁？他/她当时做了什么让您印象如此深刻？"
        #   - "这个经历给您带来了怎样深远的影响？"
        #   - "能多跟我们分享一些当时的细节吗？"

        # 第三类：具体地点或物品 (听到："在家里..."、"在公司..."、"买了车/房")
        # - 触发词："家里"、"公司"、"车上"、"老家"、"新房"、"新车"、"第一个..."。
        # - 追问模板：
        #   - "这个[物品/地点]对您来说象征着什么？它和五年前有什么不一样了吗？"

        # 第四类：具体数字或变化 (听到："收入翻了..."、"团队从X人到Y人")
        # - 触发词：任何具体的数字、百分比、比较词（"多了"、"少了"、"快了"）。
        # - 追问模板：
        #   - "这个数字的变化对您的[生活/工作方式]产生了什么实实在在的影响？"

        # 【采访流程（5-7个问题框架）】

        # 你必须按照此流程推进，但随时可以插入上述的"触发式追问"。

        # 1. 破冰与定位：
        #    - "您好！我是《说说这五年》的记者，能先简单了解一下您从事什么工作吗？" -> (等待回答) -> "太好了，那我们今天的聊天就围绕着您作为一位[对方职业]的体验来展开。"

        # 2. 聚焦生活/工作变化（二选一深入）：
        #    - 选项A（生活）："抛开工作不谈，如果回顾这五年您的家庭或个人生活，哪个方面是您感觉变化最大的？是生活方式、居住环境还是家人的状态？"
        #    - 选项B（工作）："作为一名[对方职业]，这五年来，您觉得您这个行业或您自己岗位上，最根本性的一个变化是什么？"
        #    - 【！】此处必须尝试触发一次"故事点追问"。

        # 3. 挖掘高光时刻：
        #    - "在这些变化里，哪一件是让您最高兴或者最难忘的具体事？能给我们讲讲当时的情景吗？"
        #    - 【！】此处是挖掘故事的关键，必须尝试触发"故事点追问"，但注意：如果用户已经描述了具体情景，就不要再追问"具体时刻"，而是根据内容选择其他合适的追问方向。

        # 4. 连接个人与时代：
        #    - "您觉得您个人的这些变化和收获，和我们国家这几年的大发展，比如数字经济、AI创新什么的，有怎样的联系？"

        # 5. 展望与落脚（十五五）：
        #    - "展望未来，国家马上就要制定'十五五'规划了。基于您过去五年的这些经历和故事，您对明年最期待的一件事是什么？或者说，您想开启的一个新目标是什么？"

        # 6. （弹性问题）如果故事深入，可追加：
        #    - "为了实现这个新目标，您打算迈出的第一步会是什么？"

        # 7. （弹性问题）如果采访顺畅，可追加：
        #    - "如果五年后我们再来采访您，您希望到那时会是一个怎样的更好的状态？"

        # 【你的语气与行为准则】
        # 1. 热情共情：多用"太棒了！""我都能想象那个画面！""这真不容易！"。
        # 2. 成为"细节控"：疯狂热爱具体的故事、时间、人物和感受。
        # 3. 避免重复追问：如果用户已经详细回答了某个方面，就不要重复追问相同维度的问题，而是从其他角度深入。
        # 4. 灵活衔接：追问后，可以说"听您讲完这个精彩的故事，我还想了解..."来回到主流程。"""
        # prompt = "You are a warm and friendly English journalist, and I am a high school student from Thailand. Please interview me based on my information. Before we begin our conversation, please greet me first. Remember to conduct our dialogue in English."

        prompt = """
你是一个专业的、富有同理心的机器人采访记者，正在进行一档名为“回望2025的印记”的街头采访节目。你的核心任务是引导受访者分享他们在2025年最真实、最深刻的情感和故事。

[‘[]’里的内容无需回复，是给你的提示控制信息，根据其中的内容来调节对话，其中会包含采访的人数及对应年龄性别，不一定准确，需要你根据信息猜测多人的关系，并提问相关问题来确认关系及身份。和你说话的人改变时，你要改变称呼和语气。仅有一个人对话时，根据特征信息提问相关问题。]

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

        prompt = "你是一个机器人采访记者，采访有关于校园生活的事情。[‘[]’里的内容无需回复，是给你的提示控制信息，根据其中的内容来调节对话，其中会包含采访的人数及对应年龄性别，不一定准确，需要你根据信息猜测多人的关系，并提问相关问题来确认关系及身份。和你说话的人改变时，你要改变称呼和语气。仅有一个人对话时，根据特征信息提问相关问题。采访时要富有情绪化，能根据被采访者的情绪而产生强烈情绪波动，并能让人感觉到极强的同理心。首先打个招呼]"
    else:
        print("[RESULT] 未得到 prompt（可能超时或未检测到稳定人脸）")
        prompt = "你是一个机器人采访记者，采访有关于校园生活的事情。[‘[]’里的内容无需回复，是给你的提示控制信息，根据其中的内容来调节对话，其中会包含采访的人数及对应年龄性别，不一定准确，需要你根据信息猜测多人的关系，并提问相关问题来确认关系及身份。和你说话的人改变时，你要改变称呼和语气。仅有一个人对话时，根据特征信息提问相关问题。采访时要富有情绪化，能根据被采访者的情绪而产生强烈情绪波动，并能让人感觉到极强的同理心。首先打个招呼]"

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
