# main_dual_mic.py
"""
双麦克风独立识别主入口文件
通过两个独立的麦克风采集语音，分别进行 ASR 识别，并将识别结果打上说话人标签后注入到对话系统
"""
import asyncio
import sys

import config
from dialog_session import DialogSession
from dual_mic_asr import AsyncASRWorker


def build_system_prompt() -> str:
    """
    构建系统提示词，告知模型如何区分说话人
    """
    return """你是一个智能访谈机器人，正在主持一场访谈活动。

重要说明：
- 输入文本前会有说话人标签前缀，用于区分不同的说话人：
  - "嘉宾：" 表示这是嘉宾说的话
  - "辅助记者：" 表示这是辅助记者说的话
- 请根据这些标签准确理解是谁在说话，并做出相应的回应
- 当嘉宾提问时，请用专业、友好的语气回答
- 当辅助记者补充信息时，请适时回应并引导访谈继续
- 保持对话的自然流畅，不要重复说话人的标签

请开始访谈，等待嘉宾和辅助记者的发言。"""


def on_text_recognized(session: DialogSession, text: str, speaker_label: str, prefix: str):
    """
    ASR 识别回调函数
    当识别到语音时，将文本打上说话人标签后注入到对话系统
    
    Args:
        session: DialogSession 实例
        text: 识别的文本内容
        speaker_label: 说话人标签（如 "嘉宾" 或 "辅助记者"）
        prefix: 文本前缀（如 "嘉宾：" 或 "辅助记者："）
    """
    print(f"[MAIN] {speaker_label} 识别到文本：{text!r}")
    
    # 异步注入文本到对话系统
    asyncio.create_task(session.inject_tagged_text(text, prefix))


async def main():
    """
    主函数：初始化并启动双麦克风独立识别系统
    """
    print("=" * 60)
    print("双麦克风独立识别系统")
    print("=" * 60)
    
    # 检查麦克风配置
    if config.GUEST_MIC_INDEX is None:
        print("[ERROR] 未配置嘉宾麦克风索引 (config.GUEST_MIC_INDEX)")
        print("[INFO] 请先运行以下命令查看可用麦克风设备：")
        print("       python dual_mic_asr.py")
        print("[INFO] 然后在 config.py 中设置正确的设备索引")
        sys.exit(1)
    
    if config.ASSISTANT_MIC_INDEX is None:
        print("[ERROR] 未配置辅助记者麦克风索引 (config.ASSISTANT_MIC_INDEX)")
        print("[INFO] 请先运行以下命令查看可用麦克风设备：")
        print("       python dual_mic_asr.py")
        print("[INFO] 然后在 config.py 中设置正确的设备索引")
        sys.exit(1)
    
    print(f"[CONFIG] 嘉宾麦克风索引: {config.GUEST_MIC_INDEX}")
    print(f"[CONFIG] 辅助记者麦克风索引: {config.ASSISTANT_MIC_INDEX}")
    print(f"[CONFIG] ASR 服务地址: {config.ASR_CONFIG['url']}")
    print()
    
    # 构建系统提示词
    start_prompt = build_system_prompt()
    print(f"[SYSTEM] 系统提示词已生成")
    print()
    
    # 初始化 DialogSession（关闭音频采集模式）
    print("[DIALOG] 初始化对话会话（纯文本输入模式）...")
    session = DialogSession(
        ws_config=config.ws_connect_config,
        start_prompt=start_prompt,
        output_audio_format="pcm",
        audio_file_path="",
        enable_audio_capture=False,  # 关闭音频采集，使用纯文本输入
    )
    
    # 创建外部停止事件
    stop_event = asyncio.Event()
    session.attach_stop_event(stop_event)
    
    # 初始化嘉宾 ASR Worker
    print(f"[ASR] 初始化嘉宾 ASR Worker（麦克风索引: {config.GUEST_MIC_INDEX}）...")
    guest_worker = AsyncASRWorker(
        speaker_label="嘉宾",
        device_index=config.GUEST_MIC_INDEX,
        on_text_recognized=lambda text, label: on_text_recognized(
            session, text, label, "嘉宾："
        ),
        asr_config=config.ASR_CONFIG,
        audio_config=config.ASR_AUDIO_CONFIG,
        vad_config=config.ASR_VAD_CONFIG,
    )
    
    # 初始化辅助记者 ASR Worker
    print(f"[ASR] 初始化辅助记者 ASR Worker（麦克风索引: {config.ASSISTANT_MIC_INDEX}）...")
    assistant_worker = AsyncASRWorker(
        speaker_label="辅助记者",
        device_index=config.ASSISTANT_MIC_INDEX,
        on_text_recognized=lambda text, label: on_text_recognized(
            session, text, label, "辅助记者："
        ),
        asr_config=config.ASR_CONFIG,
        audio_config=config.ASR_AUDIO_CONFIG,
        vad_config=config.ASR_VAD_CONFIG,
    )
    
    print()
    print("=" * 60)
    print("系统初始化完成，开始运行...")
    print("=" * 60)
    print()
    print("[INFO] 请嘉宾和辅助记者开始发言")
    print("[INFO] 按 Ctrl+C 停止程序")
    print()
    
    try:
        # 启动所有组件
        # 1. 启动对话会话
        dialog_task = asyncio.create_task(session.start())
        
        # 2. 启动嘉宾 ASR Worker
        guest_task = asyncio.create_task(guest_worker.start())
        
        # 3. 启动辅助记者 ASR Worker
        assistant_task = asyncio.create_task(assistant_worker.start())
        
        # 等待停止事件
        await stop_event.wait()
        
        print()
        print("[MAIN] 收到停止信号，正在关闭...")
        
        # 停止所有组件
        guest_worker.stop()
        assistant_worker.stop()
        session.stop()
        
        # 等待所有任务完成
        await asyncio.gather(
            guest_task,
            assistant_task,
            dialog_task,
            return_exceptions=True,
        )
        
        print("[MAIN] 所有组件已停止")
        
    except KeyboardInterrupt:
        print()
        print("[MAIN] 用户中断，正在关闭...")
        guest_worker.stop()
        assistant_worker.stop()
        session.stop()
        
    except Exception as e:
        print(f"[ERROR] 主程序异常: {e}")
        import traceback
        traceback.print_exc()
        
        # 确保清理资源
        try:
            guest_worker.stop()
        except:
            pass
        try:
            assistant_worker.stop()
        except:
            pass
        try:
            session.stop()
        except:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print("[MAIN] 程序已退出")
