# async_app.py
import asyncio

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector


async def main():
    # 得到prompt
    camera = CameraAdapter(
        kind="ros1",
        ros_topic="/camera/color/image_raw",
        ros_compressed=False,  # 原始 Image
        ros_queue_size=5,
        ros_node_name="fpd_subscriber",
    )

    detector = FacePromptDetector(
        camera=camera,
        interval_sec=0.5,
        required_consecutive=2,
        detector_backend="opencv",
    )

    # 先做一次 prompt 检测（内部会启动相机，但不再停止）
    prompt = detector.run(timeout=35.0)

    # 启动“持续情绪推送”（UDP -> 127.0.0.1:5555，每 2 秒一帧）
    # 若需要跨机推送，填对端 IP 即可，如 detector.start_emotion_stream("192.168.1.50", 5555, 2.0)
    detector.start_emotion_stream(host="127.0.0.1", port=5555, interval_sec=2.0)

    # 打印
    if prompt:
        print("[RESULT] prompt =", prompt)
    else:
        print("[RESULT] 未得到 prompt（可能超时或未检测到稳定人脸）")


    prompt+="记住，你是一个导航机器人。文化宫先往左走再往前走，少年宫先往右边走再往左边走。不要复述，后面问到你的时候合理的回答我。"
    # 之后按你原来的流程启动语音会话
    stop_event = asyncio.Event()
    session = DialogSession(
        config.ws_connect_config,
        start_prompt=prompt,
        output_audio_format="pcm",
    )
    session.attach_stop_event(stop_event)

    task = asyncio.create_task(session.start())
    await asyncio.sleep(200)
    stop_event.set()
    await task

    # 注意：这里不调用 detector.stop_emotion_stream() 和 camera.stop()，
    # 让持续推送保持运行直到进程退出（如需结束再手动调用）。
    # 如果你希望程序结束时释放：
    # detector.stop_emotion_stream()
    # camera.stop()


asyncio.run(main())