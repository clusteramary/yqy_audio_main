# main_dual_mic.py
"""
双麦克风语音交互主入口

用于三人深度采访场景：
- 嘉宾（Guest）：被采访对象
- 辅助记者（Assistant）：提供补充提问和话题过渡
- 机器人主持人：控制全局，负责核心提问和深度挖掘

工作原理：
1. 两个独立的麦克风分别监听嘉宾和辅助记者的语音
2. 每个麦克风有独立的 VAD + ASR 流程
3. 识别出的文本带上说话人标签后，发送给对话系统
4. 对话系统根据标签区分说话人，进行相应的回复

使用方法：
1. 运行 `python dual_mic_asr.py` 查看可用麦克风设备列表
2. 在 config.py 中设置 GUEST_MIC_INDEX 和 ASSISTANT_MIC_INDEX
3. 运行 `python main_dual_mic.py` 启动双麦克风交互
"""

import asyncio
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import config
from dialog_session import DialogSession
from dual_mic_asr import DualMicManager, print_audio_devices


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


def build_dual_mic_system_prompt(guest_info: dict) -> str:
    """
    构建双麦克风场景专用的 System Prompt。
    包含三方角色定义和输入信号说明。
    """
    
    core_role = f"""你是资深访谈主持人，正在主持一场企业家深度访谈。

【三方角色】
- 你（主持人）: 控制全局，负责核心提问和深度挖掘
- {guest_info['name']}（嘉宾）: {guest_info['company']}负责人，{guest_info['background']}
- 辅助记者: 提供补充视角和话题过渡

【访谈聚焦】{guest_info['industry']} - 重点：{' / '.join(guest_info['focus_areas'])}"""

    # ========== 关键：输入信号说明 ==========
    input_signal = """
【输入信号说明 - 重要！】
你收到的用户输入将包含身份前缀，请根据前缀区分说话人：
- 开头为 **"【嘉宾】说："** → 这是被采访对象在回答你的问题。请针对其内容进行追问或评价。
- 开头为 **"【辅助记者】说："** → 这是你的搭档在补充提问或引导话题。请根据他的引导配合推进流程。

示例输入：
  "【嘉宾】说：我们公司今年在AI领域投入了很多资源。"
  → 你应该针对嘉宾的回答追问细节，如"投入的资源主要用在哪些方向？"

  "【辅助记者】说：张总，时间快到了，我们换个话题吧。"
  → 你应该配合辅助记者的引导，礼貌地过渡到下一个话题。"""

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

    return "\n".join([core_role, input_signal, hosting_style, questioning, collaboration, language, opening])


class DualMicDialogApp:
    """
    双麦克风对话应用主类
    
    管理：
    - DialogSession：对话会话（纯文本模式）
    - DualMicManager：双麦克风 ASR 管理器
    """

    def __init__(
        self,
        guest_mic_index: int,
        assistant_mic_index: int,
        guest_profile: dict,
    ):
        self.guest_mic_index = guest_mic_index
        self.assistant_mic_index = assistant_mic_index
        self.guest_profile = guest_profile

        # 构建 System Prompt
        self.system_prompt = build_dual_mic_system_prompt(guest_profile)

        # 对话会话（稍后初始化）
        self.session: Optional[DialogSession] = None
        self.session_loop: Optional[asyncio.AbstractEventLoop] = None
        self.session_thread: Optional[threading.Thread] = None

        # 双麦克风管理器（稍后初始化）
        self.dual_mic_manager: Optional[DualMicManager] = None

        # 停止标志
        self.stop_event = asyncio.Event()
        self.running = False

        # 文本注入队列（线程安全）
        self._text_queue: asyncio.Queue = asyncio.Queue()

    def _on_guest_text(self, text: str) -> None:
        """嘉宾语音识别回调"""
        if not text or not text.strip():
            return
        print(f"\n[嘉宾] 识别结果: {text}")
        # 将文本放入队列，由主事件循环处理
        asyncio.run_coroutine_threadsafe(
            self._enqueue_text(text, "guest"),
            self.session_loop
        )

    def _on_assistant_text(self, text: str) -> None:
        """辅助记者语音识别回调"""
        if not text or not text.strip():
            return
        print(f"\n[辅助记者] 识别结果: {text}")
        # 将文本放入队列，由主事件循环处理
        asyncio.run_coroutine_threadsafe(
            self._enqueue_text(text, "assistant"),
            self.session_loop
        )

    async def _enqueue_text(self, text: str, label: str) -> None:
        """将带标签的文本加入队列"""
        await self._text_queue.put((text, label))

    async def _text_injection_loop(self) -> None:
        """文本注入循环：从队列取出文本并发送给对话系统"""
        while self.running:
            try:
                # 等待队列中的文本
                text, label = await asyncio.wait_for(
                    self._text_queue.get(),
                    timeout=0.5
                )
                # 注入到对话系统
                if self.session and self.session.is_running:
                    await self.session.inject_tagged_text(text, label)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[TEXT-INJECT] 文本注入失败: {e}")

    async def _run_session(self) -> None:
        """运行对话会话"""
        # 创建会话（纯文本模式，不初始化音频输入）
        self.session = DialogSession(
            ws_config=config.ws_connect_config,
            start_prompt=self.system_prompt,
            output_audio_format="pcm",
            audio_file_path="",  # 非音频文件模式
        )
        self.session.attach_stop_event(self.stop_event)

        # 启动文本注入循环
        injection_task = asyncio.create_task(self._text_injection_loop())

        try:
            # 启动纯文本模式会话
            await self.session.start_text_only_mode()
        finally:
            injection_task.cancel()
            try:
                await injection_task
            except asyncio.CancelledError:
                pass

    def start(self) -> None:
        """启动双麦克风对话应用"""
        if self.running:
            print("应用已在运行")
            return

        self.running = True

        # 创建并启动会话线程
        def session_thread_target():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.session_loop = loop
            try:
                loop.run_until_complete(self._run_session())
            finally:
                loop.close()

        self.session_thread = threading.Thread(target=session_thread_target, daemon=True)
        self.session_thread.start()

        # 等待会话初始化完成
        print("等待对话会话初始化...")
        time.sleep(3)

        # 创建并启动双麦克风管理器
        self.dual_mic_manager = DualMicManager(
            guest_device_index=self.guest_mic_index,
            assistant_device_index=self.assistant_mic_index,
            on_guest_text=self._on_guest_text,
            on_assistant_text=self._on_assistant_text,
        )
        self.dual_mic_manager.start()

        print("\n" + "=" * 60)
        print("双麦克风对话系统已启动！")
        print(f"  嘉宾麦克风: 设备索引 {self.guest_mic_index}")
        print(f"  辅助记者麦克风: 设备索引 {self.assistant_mic_index}")
        print("=" * 60)
        print("\n请开始对话，按 Ctrl+C 停止...\n")

    def stop(self) -> None:
        """停止双麦克风对话应用"""
        if not self.running:
            return

        print("\n正在停止双麦克风对话系统...")
        self.running = False

        # 停止双麦克风
        if self.dual_mic_manager:
            self.dual_mic_manager.stop()

        # 停止会话
        if self.session:
            self.session.stop()

        # 设置停止事件
        if self.session_loop and not self.session_loop.is_closed():
            self.session_loop.call_soon_threadsafe(self.stop_event.set)

        # 等待会话线程结束
        if self.session_thread and self.session_thread.is_alive():
            self.session_thread.join(timeout=3.0)

        print("双麦克风对话系统已停止")


def main():
    """主入口函数"""
    print("\n" + "=" * 60)
    print("双麦克风语音交互系统")
    print("用于三人深度采访场景：嘉宾 + 辅助记者 + 机器人主持人")
    print("=" * 60)

    # 打印可用设备
    print_audio_devices()

    # 从配置读取麦克风索引
    guest_mic_index = getattr(config, "GUEST_MIC_INDEX", 1)
    assistant_mic_index = getattr(config, "ASSISTANT_MIC_INDEX", 2)

    print(f"\n当前配置:")
    print(f"  嘉宾麦克风索引: {guest_mic_index}")
    print(f"  辅助记者麦克风索引: {assistant_mic_index}")
    print(f"\n如需修改，请编辑 config.py 中的 GUEST_MIC_INDEX 和 ASSISTANT_MIC_INDEX")

    # 确认是否继续
    try:
        input("\n按 Enter 键开始，或按 Ctrl+C 取消...")
    except KeyboardInterrupt:
        print("\n已取消")
        return

    # 创建应用
    app = DualMicDialogApp(
        guest_mic_index=guest_mic_index,
        assistant_mic_index=assistant_mic_index,
        guest_profile=GUEST_PROFILE,
    )

    # 设置信号处理
    def signal_handler(sig, frame):
        print("\n收到停止信号...")
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动应用
    app.start()

    # 主循环
    try:
        while app.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


if __name__ == "__main__":
    main()
