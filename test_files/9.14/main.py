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
        kind="realsense", width=640, height=480, fps=30, warmup_frames=5
    )
    detector = FacePromptDetector(
        camera=camera,
        interval_sec=0.5,
        required_consecutive=2,
        detector_backend="opencv",  # 保持与原逻辑一致
        enforce_detection=True,
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
    await asyncio.sleep(200)
    stop_event.set()
    await task


asyncio.run(main())
