# face_prompt_detector_refactored.py
import threading
import time
from typing import Optional, Union

import cv2
import numpy as np

from CameraAdapter import CameraAdapter

try:
    import pyrealsense2 as rs

    _HAS_REALSENSE = True
except Exception:
    _HAS_REALSENSE = False

from deepface import DeepFace


class FacePromptDetector:
    """
    每 interval_sec 秒用 DeepFace 检测一次来自 camera 的最新帧；
    连续 required_consecutive 次有人脸后，构造 prompt 并返回。
    """

    def __init__(
        self,
        camera: CameraAdapter,
        interval_sec: float = 0.5,
        required_consecutive: int = 2,
        detector_backend: str = "opencv",
        enforce_detection: bool = True,  # False：无脸不抛异常，方便“清零重来”
    ):
        self.camera = camera
        self.interval_sec = interval_sec
        self.required_consecutive = required_consecutive
        self.detector_backend = detector_backend
        self.enforce_detection = enforce_detection

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
            age = age - 4
            parts.append(f"年龄“{age}岁”")
        features_str = "，".join(parts) if parts else "（无可靠特征）"

        prompt = (
            "你是一位温暖、有同理心的聊天伙伴。"
            f"我给你一些参考信息：{features_str}。"
            "着重复述我的年龄，向我提问"
        )
        return prompt

    # ---------------- 分析线程 ----------------
    def _periodic_analysis(self):
        try:
            while not self._stop_event.is_set():
                time.sleep(self.interval_sec)

                frame = self.camera.read_latest_frame()
                if frame is None:
                    self._face_counter = 0
                    continue

                try:
                    results = DeepFace.analyze(
                        img_path=frame,  # ndarray(BGR)
                        actions=["age", "gender", "race", "emotion"],
                        detector_backend=self.detector_backend,
                        # DeepFace 低版本不支持 prog_bar 参数，勿传
                        # enforce_detection=self.enforce_detection,  # 有些版本此参数存在兼容性问题
                    )

                    # 防御式：确保确实拿到结果
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
                        self._prompt_result = self._build_prompt_from_face(faces[0])
                        self._stop_event.set()
                        break

                except Exception as e:
                    print(f"[periodic_analysis error] {e}")
                    self._face_counter = 0
        finally:
            # 退出时不负责关闭相机，由 run() 统一关闭
            pass

    # ---------------- 外部主入口 ----------------
    def run(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        启动相机与分析；若在 timeout 秒内连续命中 required_consecutive 次有人脸，返回 prompt；
        若超时或异常中断，返回 None。
        """
        self._stop_event.clear()
        self._face_counter = 0
        self._prompt_result = None

        # 启动相机
        self.camera.start()

        # 启动分析线程
        analyzer_thread = threading.Thread(target=self._periodic_analysis, daemon=True)
        analyzer_thread.start()

        # 等待完成或超时
        start_t = time.time()
        while not self._stop_event.is_set():
            time.sleep(0.05)
            if timeout is not None and (time.time() - start_t) > timeout:
                print("[INFO] 超时未检测到满足条件的人脸，结束。")
                self._stop_event.set()
                break

        analyzer_thread.join(timeout=1.0)
        # 关闭相机
        self.camera.stop()
        return self._prompt_result
