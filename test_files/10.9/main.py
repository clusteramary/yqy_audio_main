# async_app.py
import asyncio

from pynput import keyboard  # 需要安装：pip install pynput

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector
from keyListener import KeyListener


async def main_loop():
    """主循环逻辑：人脸识别 -> 语音交互"""
    # 初始化相机
    camera = CameraAdapter(
        kind="ros1",
        ros_topic="/camera/color/image_raw",
        ros_compressed=False,
        ros_queue_size=5,
        ros_node_name="fpd_subscriber",
    )

    # 初始化人脸检测器
    detector = FacePromptDetector(
        camera=camera,
        interval_sec=0.5,
        required_consecutive=2,
        detector_backend="opencv",
    )

    # 人脸识别阶段
    print("等待人脸识别...")
    prompt = detector.run(timeout=35.0)

    # 启动情绪推送
    detector.start_emotion_stream(host="127.0.0.1", port=5555, interval_sec=2.0)

    # 处理识别结果
    if prompt:
        print(f"[RESULT] prompt = {prompt}")
        prompt += "以下无需回复：你是一个导航机器人。文化宫先往左走再往前走，少年宫先往右边走再往左边走。不要复述，后面问到你的时候合理的回答我。"
    else:
        print("[RESULT] 未得到 prompt（可能超时或未检测到稳定人脸）")
        prompt = "你是一个导航机器人。请提供导航帮助。"

    # 语音交互阶段
    stop_event = asyncio.Event()
    session = DialogSession(
        config.ws_connect_config,
        start_prompt=prompt,
        output_audio_format="pcm",
    )
    session.attach_stop_event(stop_event)

    # 创建键盘监听器
    key_listener = KeyListener()
    key_listener.start()
    
    # 启动语音会话
    dialog_task = asyncio.create_task(session.start())
    
    # 等待任意一个任务完成
    try:
        while not stop_event.is_set() and not key_listener.p_pressed:
            await asyncio.sleep(0.1)
    finally:
        if not stop_event.is_set():
            stop_event.set()
        
        # 停止键盘监听
        key_listener.stop()
        
        # 等待任务完成
        await asyncio.sleep(0.1)
        
        # 停止情绪推送和相机
        detector.stop_emotion_stream()
        camera.stop()
        
        # 取消可能还在运行的任务
        if not dialog_task.done():
            dialog_task.cancel()
            try:
                await dialog_task
            except asyncio.CancelledError:
                pass


async def main():
    """主函数，支持循环运行"""
    while True:
        await main_loop()
        print("程序结束，按'p'键重新开始或按其他键退出...")
        
        # 等待用户决定是重新开始还是退出
        key_listener = KeyListener()
        key_listener.start()
        
        try:
            # 等待按键
            while True:
                if key_listener.p_pressed:
                    print("重新开始程序...")
                    break
                
                # 检查是否有其他按键
                with keyboard.Events() as events:
                    event = events.get(0.1)
                    if event and not isinstance(event, keyboard.Events.Press):
                        print("退出程序...")
                        return
                
                await asyncio.sleep(0.1)
        finally:
            key_listener.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")