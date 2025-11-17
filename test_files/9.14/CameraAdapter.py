# face_prompt_detector_refactored.py
import threading
import time
from typing import Optional, Union

import cv2
import numpy as np

try:
    import pyrealsense2 as rs

    _HAS_REALSENSE = True
except Exception:
    _HAS_REALSENSE = False

from deepface import DeepFace


class CameraAdapter:
    """
    相机适配器：统一“启动采集/停止采集/读取最新帧”的接口。
    - kind="realsense": 使用 Intel RealSense 彩色流
    - kind="opencv":    使用 OpenCV VideoCapture（USB/笔记本摄像头）
    """

    def __init__(
        self,
        kind: str = "realsense",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        warmup_frames: int = 5,
        # 仅当 kind="opencv" 时有效
        opencv_index: int = 0,
    ):
        self.kind = kind.lower()
        self.width = width
        self.height = height
        self.fps = fps
        self.warmup_frames = warmup_frames
        self.opencv_index = opencv_index

        self._last_frame = None  # 最新一帧（BGR, np.ndarray）
        self._last_frame_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 运行期资源
        self._rs_pipeline = None
        self._rs_config = None
        self._cv_cap = None

        if self.kind == "realsense" and not _HAS_REALSENSE:
            raise RuntimeError("未检测到 pyrealsense2，无法使用 kind='realsense'。")

    # ----------- 公共API -----------
    def start(self):
        """启动后台采集线程。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止采集线程并释放资源。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._release_resources()

    def read_latest_frame(self) -> Optional[np.ndarray]:
        """返回最近一帧（BGR），未就绪时返回 None。"""
        with self._last_frame_lock:
            if self._last_frame is None:
                return None
            return self._last_frame.copy()

    # ----------- 内部实现 -----------
    def _capture_loop(self):
        try:
            if self.kind == "realsense":
                self._init_realsense()
                # 预热
                for _ in range(self.warmup_frames):
                    self._rs_pipeline.wait_for_frames()

                while not self._stop_event.is_set():
                    frames = self._rs_pipeline.wait_for_frames()
                    color_frame = frames.get_color_frame()
                    if not color_frame:
                        continue
                    color = np.asanyarray(color_frame.get_data())  # (H,W,3) uint8 BGR
                    with self._last_frame_lock:
                        self._last_frame = color

            elif self.kind == "opencv":
                self._init_opencv()
                # 预热
                for _ in range(self.warmup_frames):
                    self._cv_cap.read()

                # 主循环
                while not self._stop_event.is_set():
                    ok, frame = self._cv_cap.read()
                    if not ok:
                        time.sleep(0.01)
                        continue
                    # 保证尺寸（某些驱动不会按 set 设置分辨率）
                    if (frame.shape[1] != self.width) or (
                        frame.shape[0] != self.height
                    ):
                        frame = cv2.resize(frame, (self.width, self.height))
                    with self._last_frame_lock:
                        self._last_frame = frame

            else:
                raise ValueError(f"不支持的相机类型 kind={self.kind!r}")

        except Exception as e:
            print(f"[CameraAdapter ERROR] 采集失败：{e}")
        finally:
            self._release_resources()

    def _init_realsense(self):
        self._rs_pipeline = rs.pipeline()
        self._rs_config = rs.config()
        self._rs_config.enable_stream(
            rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps
        )
        self._rs_pipeline.start(self._rs_config)
        print("[CameraAdapter] RealSense 采集已启动。")

    def _init_opencv(self):
        self._cv_cap = cv2.VideoCapture(self.opencv_index, cv2.CAP_DSHOW)
        # 尽可能设置（有些平台可能忽略）
        self._cv_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cv_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cv_cap.set(cv2.CAP_PROP_FPS, self.fps)
        if not self._cv_cap.isOpened():
            raise RuntimeError(f"无法打开 OpenCV 摄像头 index={self.opencv_index}")
        print("[CameraAdapter] OpenCV 摄像头采集已启动。")

    def _release_resources(self):
        if self._cv_cap is not None:
            try:
                self._cv_cap.release()
            except Exception:
                pass
            self._cv_cap = None

        if self._rs_pipeline is not None:
            try:
                self._rs_pipeline.stop()
            except Exception:
                pass
            self._rs_pipeline = None

        print("[CameraAdapter] 采集资源已释放。")
