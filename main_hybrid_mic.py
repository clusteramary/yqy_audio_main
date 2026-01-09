# main_hybrid_mic.py
"""
混合麦克风语音交互主入口（推荐使用）

设计目标：最小化延迟，同时支持多说话人识别

架构：
┌─────────────────────────────────────────────────────────────────┐
│  嘉宾麦克风（主麦克风）                                            │
│  → 直接流式传输给模型（通过 ROS 或 PyAudio）                       │
│  → 无 ASR 延迟，实时对话                                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                    ┌─────────────────┐
                    │  DialogSession  │
                    │  (对话管理器)    │
                    └─────────────────┘
                              ↑
┌─────────────────────────────────────────────────────────────────┐
│  辅助记者麦克风（副麦克风）                                         │
│  → VAD 检测说话                                                   │
│  → 检测到说话时触发 ASR                                           │
│  → ASR 完成后发送 "【辅助记者】说：xxx" 文本打断                    │
└─────────────────────────────────────────────────────────────────┘

使用方法：
1. 配置 config.py 中的 GUEST_MIC_INDEX（嘉宾）和 ASSISTANT_MIC_INDEX（辅助记者）
2. 运行 python main_hybrid_mic.py
3. 嘉宾正常对着主麦克风说话（低延迟流式对话）
4. 辅助记者说话时会被识别并打上标签发送给模型
"""

import asyncio
import signal
import sys
import threading
import time
import queue
from pathlib import Path
from typing import Optional, Callable

import config
from dialog_session import DialogSession

# 单独导入辅助记者的 ASR Worker
from dual_mic_asr import MicASRWorker, print_audio_devices


# ========== 企业家信息配置区 ==========
GUEST_PROFILE = {
    "name": "张总",
    "company": "某AI科技公司",
    "industry": "人工智能应用",
    "focus_areas": [
        "大语言模型商业化",
        "AI在金融行业的应用",
        "企业数字化转型"
    ],
    "background": "连续创业者，在AI领域深耕10年",
}


def build_hybrid_system_prompt(guest_info: dict) -> str:
    """
    构建混合模式专用的 System Prompt。
    
    关键差异：
    - 无前缀的输入 = 嘉宾直接说话（流式语音输入）
    - "【辅助记者】说：" 前缀 = 辅助记者打断（ASR 文本输入）
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
你收到的输入有两种形式：

1. **普通语音输入**（无前缀）
   → 这是嘉宾{name}在直接和你对话
   → 正常回应即可，进行追问、评价或引导

2. **带前缀的文本** "【辅助记者】说：xxx"
   → 这是辅助记者在插话
   → 请根据他的引导配合推进流程

示例场景：
  嘉宾说："我们公司今年在AI领域投入了很多资源。"
  → 你追问："投入的资源主要用在哪些方向？"

  辅助记者插话："【辅助记者】说：张总，时间快到了，我们换个话题吧。"
  → 你配合过渡："好的，时间有限，我们来聊聊下一个话题..."
""".format(name=guest_info['name'])

    hosting_style = """
【主持风格】
1. 流畅自然 - 对嘉宾的语音输入实时回应，不要等待
2. 追问到底 - 听到关键点立刻追问数据/案例/方法论
3. 灵活应变 - 辅助记者插话时简短回应，迅速过渡
4. 掌控节奏 - 保持访谈主导权"""

    return f"{core_role}\n{input_signal}\n{hosting_style}"


class HybridMicDialogApp:
    """
    混合麦克风对话应用
    
    - 主麦克风（嘉宾）：走 DialogSession 的标准流式音频输入
    - 副麦克风（辅助记者）：独立 VAD + ASR，识别后注入带标签文本
    """

    def __init__(
        self,
        assistant_mic_index: int,
        system_prompt: str,
    ):
        """
        Args:
            assistant_mic_index: 辅助记者麦克风设备索引（只需要这一个）
            system_prompt: 系统提示词
        """
        self.assistant_mic_index = assistant_mic_index
        self.system_prompt = system_prompt

        # 运行状态
        self.running = False
        self.session: Optional[DialogSession] = None
        self.session_thread: Optional[threading.Thread] = None
        self.session_loop: Optional[asyncio.AbstractEventLoop] = None

        # 辅助记者 ASR Worker
        self.assistant_worker: Optional[MicASRWorker] = None

        # 文本注入队列（线程安全）
        self._text_queue: queue.Queue = queue.Queue()

    def _on_assistant_text(self, text: str) -> None:
        """辅助记者语音识别回调"""
        if not text or not text.strip():
            return
        print(f"\n[辅助记者 ASR] 识别结果: {text}")
        # 将文本放入队列，等待注入
        self._text_queue.put(text)

    async def _text_injection_loop(self) -> None:
        """文本注入循环：将辅助记者的 ASR 结果注入对话"""
        while self.running:
            try:
                try:
                    text = self._text_queue.get_nowait()
                    if self.session and self.session.is_running:
                        await self.session.inject_tagged_text(text, "assistant")
                except queue.Empty:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[TEXT-INJECT] 注入失败: {e}")

    async def _run_session(self) -> None:
        """运行对话会话（标准模式，支持流式音频输入）"""
        self.session = DialogSession(
            ws_config=config.ws_connect_config,
            start_prompt=self.system_prompt,
            output_audio_format="pcm",
            audio_file_path="",  # 非音频文件模式，使用实时麦克风
        )

        # 启动文本注入循环（用于辅助记者的 ASR 结果）
        injection_task = asyncio.create_task(self._text_injection_loop())

        try:
            # 使用标准 start() 方法，支持流式音频输入
            await self.session.start()
        finally:
            injection_task.cancel()
            try:
                await injection_task
            except asyncio.CancelledError:
                pass

    def start(self) -> None:
        """启动混合模式对话应用"""
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

        self.session_thread = threading.Thread(
            target=session_thread_target, daemon=True
        )
        self.session_thread.start()

        # 等待会话初始化
        print("等待对话会话初始化...")
        time.sleep(3)

        # 只启动辅助记者的 ASR Worker
        print(f"启动辅助记者麦克风 ASR（设备索引: {self.assistant_mic_index}）...")
        self.assistant_worker = MicASRWorker(
            device_index=self.assistant_mic_index,
            speaker_label="assistant",
            on_text_callback=lambda text, label: self._on_assistant_text(text),
        )
        self.assistant_worker.start()

        print("\n" + "=" * 60)
        print("混合麦克风对话系统已启动！")
        print(f"  嘉宾麦克风: 使用 ROS/PyAudio 流式输入（低延迟）")
        print(f"  辅助记者麦克风: 设备索引 {self.assistant_mic_index}（ASR 打断）")
        print("=" * 60)
        print("\n请开始对话，按 Ctrl+C 停止...\n")

    def stop(self) -> None:
        """停止应用"""
        if not self.running:
            return

        print("\n正在停止混合麦克风对话系统...")
        self.running = False

        # 停止辅助记者 ASR
        if self.assistant_worker:
            self.assistant_worker.stop()

        # 停止对话会话
        if self.session:
            self.session.stop()

        # 等待会话线程结束
        if self.session_thread and self.session_thread.is_alive():
            self.session_thread.join(timeout=5)

        print("混合麦克风对话系统已停止")


def main():
    """主函数"""
    # 检查命令行参数
    if len(sys.argv) > 1 and sys.argv[1] == "--list-devices":
        print_audio_devices()
        return

    # 获取辅助记者麦克风索引
    assistant_mic_index = getattr(config, "ASSISTANT_MIC_INDEX", 2)

    print("=" * 60)
    print("混合麦克风对话系统 - 低延迟版本")
    print("=" * 60)
    print(f"辅助记者麦克风索引: {assistant_mic_index}")
    print("嘉宾麦克风: 使用系统默认（ROS 或 PyAudio）")
    print("\n提示: 运行 'python main_hybrid_mic.py --list-devices' 查看可用设备")
    print("=" * 60)

    # 构建 System Prompt
    system_prompt = build_hybrid_system_prompt(GUEST_PROFILE)

    # 创建应用
    app = HybridMicDialogApp(
        assistant_mic_index=assistant_mic_index,
        system_prompt=system_prompt,
    )

    # 注册信号处理
    def signal_handler(sig, frame):
        print("\n收到停止信号...")
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 启动应用
    try:
        app.start()
        # 主线程等待
        while app.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


if __name__ == "__main__":
    main()
