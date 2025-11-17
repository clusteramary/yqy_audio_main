# face_prompt_detector.py
import threading
import time
from typing import Optional

import cv2
import numpy as np
import pyrealsense2 as rs

from deepface import DeepFace


class FacePromptDetector:
    """
    实时读取 RealSense 彩色帧，每 interval_sec 秒用 DeepFace 检测一次；
    如果连续 required_consecutive 次检测到人脸，则构造 prompt 并返回。
    """

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        warmup_frames: int = 5,
        interval_sec: float = 0.5,
        required_consecutive: int = 2,
        detector_backend: str = "opencv",
        enforce_detection: bool = True,  # False：无脸不抛异常，便于你做“清零重来”
    ):
        # 采集参数
        self.width = width
        self.height = height
        self.fps = fps
        self.warmup_frames = warmup_frames

        # 分析参数
        self.interval_sec = interval_sec
        self.required_consecutive = required_consecutive
        self.detector_backend = detector_backend
        self.enforce_detection = enforce_detection

        # 线程/共享状态
        self._last_frame = None
        self._last_frame_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._face_counter = 0
        self._prompt_result: Optional[str] = None

    # ---------------- 工具函数 ----------------
    @staticmethod
    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: FacePromptDetector._sanitize(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [FacePromptDetector._sanitize(item) for item in obj]
        elif isinstance(obj, np.generic):
            return obj.item()
        return obj

    @staticmethod
    def _build_prompt_from_face(face_dict: dict) -> str:
        face = FacePromptDetector._sanitize(face_dict)

        def pick_top(d):
            if not isinstance(d, dict) or not d:
                return None, None
            k = max(d, key=d.get)
            return k, d[k]

        dominant_emotion, emotion_pct = pick_top(face.get("emotion", {}))
        dominant_gender, gender_pct = pick_top(face.get("gender", {}))
        dominant_race, race_pct = pick_top(face.get("race", {}))
        age = face.get("age", None)
        try:
            age = int(round(float(age))) if age is not None else None
        except Exception:
            age = None

        parts = []
        if dominant_emotion is not None:
            parts.append(f"情绪“{dominant_emotion}”({emotion_pct:.1f}%)")
        if dominant_gender is not None:
            parts.append(f"性别“{dominant_gender}”({gender_pct:.1f}%)")
        if dominant_race is not None:
            parts.append(f"种族“{dominant_race}”({race_pct:.1f}%)")
        if age is not None:
            age = age - 8
            parts.append(f"年龄“{age}岁”")
        features_str = "，".join(parts) if parts else "（无可靠特征）"

        # prompt = (
        #     "你是一位温暖、有同理心的聊天伙伴。"
        #     f"我给你一些参考信息：{features_str}。"
        #     "请不要逐字复述这些内容，而是基于它们，用自然的方式跟我打招呼，"
        #     "并提出开放式问题，引导我聊聊今天的感受或最近的故事。"
        # )
        prompt = (
            "你是一位温暖、有同理心的聊天伙伴。"
            f"我给你一些参考信息：{features_str}。"
            "着重复述我的年龄，向我提问"
        )
        return prompt

    # ---------------- 分析线程 ----------------
    def _periodic_analysis(self):
        # while not self._stop_event.is_set():
        while True:
            time.sleep(self.interval_sec)

            # 取帧
            with self._last_frame_lock:
                frame = None if (self._last_frame is None) else self._last_frame.copy()

            if frame is None:
                self._face_counter = 0
                continue

            try:
                results = DeepFace.analyze(
                    img_path=frame,  # 支持 ndarray(BGR)
                    actions=["age", "gender", "race", "emotion"],
                    detector_backend=self.detector_backend,
                    # enforce_detection=self.enforce_detection,
                    # 注意：不要传 prog_bar 参数；低版本 deepface 不支持
                )

                # 某些版本可能不会抛异常，所以手动检查
                if not results or (isinstance(results, list) and len(results) == 0):
                    raise RuntimeError("未检测到人脸！")

                if isinstance(results, dict):
                    results = [results]
                faces = [r for r in results if isinstance(r, dict)]

                if len(faces) >= 1:
                    self._face_counter += 1
                else:
                    self._face_counter = 0
                    continue

                if self._face_counter >= self.required_consecutive:
                    # 生成 prompt 并结束
                    self._prompt_result = self._build_prompt_from_face(faces[0])
                    self._stop_event.set()
                    break

            except Exception as e:
                print(f"[periodic_analysis error] {e}")
                self._face_counter = 0

    # ---------------- 采集主循环 ----------------
    def _realsense_capture_loop(self):
        pipeline = None
        try:
            pipeline = rs.pipeline()
            config = rs.config()
            config.enable_stream(
                rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps
            )
            pipeline.start(config)

            # 预热
            for _ in range(self.warmup_frames):
                pipeline.wait_for_frames()

            print("[INFO] 开始采集彩色帧（仅供 DeepFace 分析）...")
            while not self._stop_event.is_set():
                frames = pipeline.wait_for_frames()
                color_frame = frames.get_color_frame()
                if not color_frame:
                    continue

                color = np.asanyarray(color_frame.get_data())  # (H,W,3) uint8, BGR
                with self._last_frame_lock:
                    self._last_frame = color

            print("[INFO] 采集线程结束。")
        except Exception as e:
            print(f"[ERROR] RealSense 采集出错：{e}")
            self._stop_event.set()
        finally:
            try:
                if pipeline is not None:
                    pipeline.stop()
            except Exception:
                pass

    # ---------------- 外部主入口 ----------------
    def run(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        启动采集+分析；若在 timeout 秒内连续两次有人脸，则返回 prompt 字符串；
        若超时或异常中断，返回 None。
        """
        self._stop_event.clear()
        self._face_counter = 0
        self._prompt_result = None

        analyzer_thread = threading.Thread(target=self._periodic_analysis, daemon=True)
        analyzer_thread.start()

        # 在当前线程跑采集循环；直到 stop_event 或超时
        start_t = time.time()
        capture_thread = threading.Thread(
            target=self._realsense_capture_loop, daemon=True
        )
        capture_thread.start()

        while not self._stop_event.is_set():
            time.sleep(0.05)
            if timeout is not None and (time.time() - start_t) > timeout:
                print("[INFO] 超时未检测到满足条件的人脸，结束。")
                self._stop_event.set()
                break

        # 等子线程收尾（给个小超时时间）
        analyzer_thread.join(timeout=1.0)
        capture_thread.join(timeout=1.0)
        return self._prompt_result
