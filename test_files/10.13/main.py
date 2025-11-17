# async_app.py
import asyncio
import time

import config
from audio_manager import DialogSession
from CameraAdapter import CameraAdapter
from FacePromptDetector import FacePromptDetector

ABSENT_SECONDS = 30.0      # ✅ 对话进行时，连续多久没看到人脸就重启
EMOTION_INTERVAL = 1.5    # 情绪线程检测频率（越小越灵敏，代价是算力更高）
INITIAL_DETECT_TIMEOUT = 35.0  # 首次做人脸特征引导的超时时间


async def monitor_face_absence(detector: FacePromptDetector,
                               stop_event: asyncio.Event,
                               absent_secs: float = ABSENT_SECONDS,
                               poll_secs: float = 0.5,
                               warmup_secs: float = 2.0):
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
                print(f"[watchdog] 启动后 {warmup_secs + absent_secs:.1f}s 仍未看到人脸，重启本轮流程。")
                stop_event.set()
                break
        else:
            if now - last_ts > absent_secs:
                print(f"[watchdog] 已 {now - last_ts:.1f}s 未检测到人脸，重启本轮流程。")
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
    detector.start_emotion_stream(host="127.0.0.1", port=5555, interval_sec=EMOTION_INTERVAL)

    # 构造起始 prompt
    if prompt:
        print(f"[RESULT] prompt = {prompt}")
        # prompt += "以下无需回复：你是一个导航机器人。文化宫先往左走再往前走，少年宫先往右边走再往左边走。不要复述，后面问到你的时候合理的回答我。"
        # prompt += "以下无需回复：现在你是一个活泼热情的记者。在之后跟我的对话中向我提问四个问题，分开提问。提问后做出简要反馈并提出下一个问题。最后在所有四个提问结束后结合提问的内容和回答主动继续对话。第一次向我提问：“在‘十四五’规划收官和‘十五五’规划开局的历史交汇点，您如何评估四中全会在确定国家中长期发展方略中的关键作用？”第二次向我提问：基于‘十四五’期间在高质量发展，比如新质生产力领域取得的成就，您认为‘十五五’规划将如何在继承与创新之间寻找平衡？”第三次和第四次提问按照之前的提问和回答智能提问。"
        prompt += "以下无需回复：现在你是一个活泼热情的记者。在之后跟我的对话中向我提问四个问题，分开提问。提问后做出简要反馈并提出下一个问题。最后在所有四个提问结束后结合提问的内容和回答主动继续对话。第一次向我提问：“您好！很高兴能与您交流。我们即将迎来关键的‘十五五’规划，大家都很期待。能先请您聊聊，您个人最关注‘十五五’规划会在哪些方面为您的生活带来新的积极变化吗？” 第二次向我提问：“您提到了对生活的期待，这很重要。我们知道，不久前闭幕的四中全会强调了‘制度建设’和‘治理效能’。在您看来，如何通过更好的制度建设，才能确保‘十五五’的宏伟蓝图能够一步步变为我们身边的现实呢？””第三次和第四次提问按照之前的提问和回答智能提问。"
        
    else:
        print("[RESULT] 未得到 prompt（可能超时或未检测到稳定人脸）")
        prompt = "现在你是一个活泼热情的记者。在之后跟我的对话中向我提问四个问题，分开提问。第一次向我提问：“在‘十四五’规划收官和‘十五五’规划开局的历史交汇点，您如何评估四中全会在确定国家中长期发展方略中的关键作用？”第二次向我提问：基于‘十四五’期间在高质量发展，比如新质生产力领域取得的成就，您认为‘十五五’规划将如何在继承与创新之间寻找平衡？”第三次和第四次提问按照之前的提问和回答智能提问。"

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
        for t in (watchdog_task, dialog_task):
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

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("程序被用户中断")