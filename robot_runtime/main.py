"""Retained ROS1 robot entry point; use interview_robot for offline research."""
import asyncio
import time

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector

ABSENT_SECONDS = 300.0
EMOTION_INTERVAL = 1.5
INITIAL_DETECT_TIMEOUT = 35.0


async def monitor_face_absence(detector, stop_event, absent_secs=ABSENT_SECONDS,
                               poll_secs=0.5, warmup_secs=2.0):
    started = time.time()
    while not stop_event.is_set():
        now = time.time()
        last = detector.get_last_face_ts()
        if (last is None and now - started > warmup_secs + absent_secs) or (
                last is not None and now - last > absent_secs):
            stop_event.set()
            return
        await asyncio.sleep(poll_secs)


async def run_once():
    camera = CameraAdapter(kind="ros1", ros_topic="/camera/color/image_raw",
                           ros_compressed=False, ros_queue_size=5, ros_node_name="fpd_subscriber")
    detector = FacePromptDetector(camera=camera, interval_sec=0.5, required_consecutive=2,
                                  detector_backend="opencv")
    stop_event = asyncio.Event()
    tasks = []
    try:
        features_prompt = detector.run(timeout=INITIAL_DETECT_TIMEOUT)
        detector.start_emotion_stream(host="127.0.0.1", port=5555, interval_sec=EMOTION_INTERVAL)
        prompt = ("你是一名机器人采访记者。先问候，再围绕用户回答逐步追问，每次只问一个问题。"
                  "以下视觉特征仅供参考，可能不准确，不要把推测当作用户确认的事实。")
        if features_prompt:
            prompt += features_prompt
        session = DialogSession(config.ws_connect_config, start_prompt=prompt, output_audio_format="pcm")
        session.attach_stop_event(stop_event)
        tasks = [asyncio.create_task(session.start()),
                 asyncio.create_task(monitor_face_absence(detector, stop_event)),
                 asyncio.create_task(stop_event.wait())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        stop_event.set()
        detector.stop_emotion_stream()
        camera.stop()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    required = ("X-Api-App-ID", "X-Api-Access-Key", "X-Api-App-Key")
    if any(not config.ws_connect_config["headers"][key] for key in required):
        raise RuntimeError("Set VOLC_DIALOG_APP_ID, VOLC_DIALOG_ACCESS_KEY and VOLC_DIALOG_APP_KEY first.")
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[robot] 会话失败：{exc}，3 秒后重启")
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")
