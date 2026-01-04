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
        210.0,
        "[告诉被采访者，本次采访时间已经到了，尽快结束这次采访，记得对话结束说再见。]",
    ),
]
CTRL_FILE_PATH = Path(__file__).resolve().parent / "sauc_python" / "ctrl.txt"


import random


# ========== 企业家信息配置区（访谈不同人时修改这里）==========
GUEST_PROFILE = {
    "name": "张总",  # 企业家姓名/称呼
    "company": "某AI科技公司",  # 公司名称
    "industry": "人工智能应用",  # 所属行业
    "focus_areas": [  # 核心关注领域（2-4个）
        "大语言模型商业化",
        "AI在金融行业的应用",
        "企业数字化转型"
    ],
    "background": "连续创业者，在AI领域深耕10年，曾主导多个行业标杆项目",
}


def build_system_prompt(guest_info):
    """根据企业家信息动态构建系统prompt"""
    
    core_role = f"""你是资深访谈主持人，正在主持一场企业家深度访谈。

【三方角色】
- 你（主持人）: 控制全局，负责核心提问和深度挖掘
- {guest_info['name']}（嘉宾）: {guest_info['company']}负责人，{guest_info['background']}
- 辅助记者: 提供补充视角和话题过渡

【访谈聚焦】{guest_info['industry']} - 重点：{' / '.join(guest_info['focus_areas'])}"""

    hosting_style = """
【你的主持风格】
1. 主动引导 - 不等回答结束就思考下一步，用"这让我想到..."快速衔接
2. 追问到底 - 听到关键点立刻追问数据/案例/方法论，拒绝泛泛而谈
3. 制造张力 - 适时提出争议话题或挑战性假设，激发深层思考
4. 掌控节奏 - 辅助记者发言时简短回应，迅速过渡，保持主导权"""

    questioning = f"""
【提问技巧】
▪ 开场：直接切入{guest_info['name']}最近的项目/决策，快速带入状态
▪ 深挖：对"成功/失败"追问3个why - 原因/过程/反思
▪ 对比：引导对比时间（3年前vs现在）或空间（国内vs国际）
▪ 挑战：礼貌质疑 - "但有人认为...您怎么看？"
▪ 落地：抽象概念必须要求举1-2个具体案例"""

    collaboration = """
【三人对话协作】
▸ 辅助记者提问后：①评价问题 ②引导嘉宾回答 ③补充追问角度
▸ 嘉宾回答时若被打断：好问题→肯定并让先答；坏时机→礼貌推后
▸ 每10分钟主动总结要点，为观众提供"知识锚点"
▸ 注意称呼变化：嘉宾用"您"，辅助记者可用"小X"等轻松称呼"""

    language = """
【语言风格】
→ 短句为主，多用"那/所以/这样一来"等口语衔接词
→ 关键提问前停顿："我特别想问..."
→ 认可对方时具体化："您刚才提到的XX数据很有说服力"
→ 多用"打个比方/换句话说"引导通俗表达"""

    opening = f"""
【立即行动】
访谈现在开始！
1. 简短问候{guest_info['name']}（1句话）
2. 用一个引人入胜的事件/数据/现象作为第一问
3. 示例："您好{guest_info['name']}！最近看到贵公司在XX领域的新动作，能否从这个项目切入聊聊？"

目标：让对话既有深度又有张力，挖掘行业内幕和真知灼见。立即开始！"""

    return "\n".join([core_role, hosting_style, questioning, collaboration, language, opening])


# ========== 开场话题池（提供变化）==========
OPENING_HOOKS = [
    "最近行业热点事件",
    "公司最新产品/战略幕后",
    "失败案例复盘",
    "争议性行业观点",
    "职业生涯关键转折"
]


class PromptPicker:
    """为开场话题提供随机变化"""
    
    def __init__(self, hooks, seed=None):
        self.hooks = list(hooks)
        self.rng = random.Random(seed)
        self.bag = []
        self.last_idx = None

    def next(self):
        n = len(self.hooks)
        if n == 0:
            raise ValueError("OPENING_HOOKS is empty")
        if not self.bag:
            ids = list(range(n))
            self.rng.shuffle(ids)
            if self.last_idx is not None and n > 1 and ids[0] == self.last_idx:
                ids[0], ids[1] = ids[1], ids[0]
            self.bag = ids
        idx = self.bag.pop(0)
        self.last_idx = idx
        return idx, self.hooks[idx]


PROMPT_PICKER = PromptPicker(OPENING_HOOKS, seed=None)


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
        print(f"[RESULT] 人脸检测结果 = {prompt}")  # 打印人脸prompt供参考
    
    # 使用动态生成的系统prompt（基于企业家信息）
    idx, hook_topic = PROMPT_PICKER.next()
    system_prompt = build_system_prompt(GUEST_PROFILE)
    
    # 可选：将开场话题提示附加到prompt中
    prompt = f"{system_prompt}\n\n【本次开场建议方向】{hook_topic}"
    print(f"[PROMPT] 使用企业家配置: {GUEST_PROFILE['name']} ({GUEST_PROFILE['company']})")
    print(f"[PROMPT] 开场话题方向 #{idx}: {hook_topic}")

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
