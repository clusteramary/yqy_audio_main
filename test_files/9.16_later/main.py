# async_app.py
import asyncio

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter

# from deepface_demo2 import FacePromptDetector
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
    prompt = detector.run(timeout=15.0)

    # 打印
    if prompt:
        print("[RESULT] prompt =", prompt)
    else:
        print("[RESULT] 未得到 prompt（可能超时或未检测到稳定人脸）")

    stop_event = asyncio.Event()
    session = DialogSession(
        config.ws_connect_config,
        start_prompt=prompt,
        output_audio_format="pcm",
    )
    session.attach_stop_event(stop_event)

    task = asyncio.create_task(session.start())
    await asyncio.sleep(100)
    stop_event.set()
    await task


asyncio.run(main())
