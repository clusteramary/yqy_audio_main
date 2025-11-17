# face_prompt_detector_refactored.py
import json
import socket
import threading
import time
from typing import Optional

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

    新增：
    - start_emotion_stream(host, port, interval)：启动一个后台线程，每 2s 抓一帧做 emotion 并通过 UDP 推送 JSON；
      与一次性 prompt 检测互不影响，可长期运行。
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

        # --- 新增：情绪推送 ---
        self._emo_thread: Optional[threading.Thread] = None
        self._emo_stop = threading.Event()
        self._emo_sock: Optional[socket.socket] = None
        self._emo_addr = ("127.0.0.1", 5555)
        self._emo_interval = 2.0
        
        # --- 新增：最近看到人脸的时间戳（给对话阶段的“看门狗”用） ---
        self._last_face_ts = None
        self._ts_lock = threading.Lock()

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
            age = age - 2
            parts.append(f"年龄“{age}岁”")
        features_str = "，".join(parts) if parts else "（无可靠特征）"

        prompt = (
            # "你是一位温暖、有同理心的聊天伙伴。"
            f"我给你一些你看到的参考信息：{features_str}。"
            # "着重复述我的年龄,向我提问"
            # "根据我的信息，向打个招呼。后面的内容记住就行，不参与第一次交互。"
            # "根据我的信息调整后面的对话。"
        )
        return prompt
    
    
    # >>> 新增：时间戳访问方法
    def _mark_face_seen(self, ts: float = None):
        if ts is None:
            ts = time.time()
        with self._ts_lock:
            self._last_face_ts = ts

    def get_last_face_ts(self) -> Optional[float]:
        with self._ts_lock:
            return self._last_face_ts

    def reset_last_face_ts(self):
        with self._ts_lock:
            self._last_face_ts = None
    # <<< 新增结束
    
    
    # ---------------- 分析线程（一次性，生成 prompt） ----------------
    # def _periodic_analysis(self):
    #     try:
    #         while not self._stop_event.is_set():
    #             time.sleep(self.interval_sec)

    #             frame = self.camera.read_latest_frame()
    #             if frame is None:
    #                 self._face_counter = 0
    #                 continue

    #             try:
    #                 results = DeepFace.analyze(
    #                     img_path=frame,  # ndarray(BGR)
    #                     actions=["age", "gender", "race", "emotion"],
    #                     detector_backend=self.detector_backend,
    #                     # DeepFace 低版本不支持 prog_bar 参数，勿传
    #                     # enforce_detection=self.enforce_detection,  # 有些版本此参数存在兼容性问题
    #                 )

    #                 # 防御式：确保确实拿到结果
    #                 if not results or (isinstance(results, list) and len(results) == 0):
    #                     raise RuntimeError("未检测到人脸！")

    #                 if isinstance(results, dict):
    #                     results = [results]
    #                 faces = [r for r in results if isinstance(r, dict)]

    #                 if len(faces) >= 1:
    #                     self._face_counter += 1
    #                 else:
    #                     self._face_counter = 0
    #                     continue

    #                 if self._face_counter >= self.required_consecutive:
    #                     self._prompt_result = self._build_prompt_from_face(faces[0])
    #                     self._stop_event.set()
    #                     break

    #             except Exception as e:
    #                 print(f"[periodic_analysis error] {e}")
    #                 self._face_counter = 0
    #     finally:
    #         # 不在这里关相机（允许后续持续推送）
    #         pass

   # ---------------- 分析线程（一次性，生成 prompt） ----------------
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
                        img_path=frame,
                        actions=["age", "gender", "race", "emotion"],
                        detector_backend=self.detector_backend,
                    )

                    if not results or (isinstance(results, list) and len(results) == 0):
                        raise RuntimeError("未检测到人脸！")

                    if isinstance(results, dict):
                        results = [results]
                    faces = [r for r in results if isinstance(r, dict)]

                    if len(faces) >= 1:
                        self._face_counter += 1
                        # ✅ 新增：一旦检测到人脸，就更新“最近看到人脸”的时间戳
                        self._mark_face_seen()
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
            pass

    # ---------------- 新增：持续情绪推送线程 ----------------
    def _emotion_loop(self):
        """
        每 self._emo_interval 秒：
          - 取最新帧
          - DeepFace 仅做 actions=["emotion"]
          - 通过 UDP 推送 JSON：{ts, has_face, dominant_emotion, emotion{...}}
        """
        sock = self._emo_sock
        addr = self._emo_addr
        interval = float(self._emo_interval)

        # while not self._emo_stop.is_set():
        #     t0 = time.time()
        #     payload = {
        #         "ts": t0,
        #         "has_face": False,
        #         "dominant_emotion": None,
        #         "emotion": {},
        #         "source": "FacePromptDetector",
        #     }
        #     frame = self.camera.read_latest_frame()
        #     if frame is not None:
        #         try:
        #             res = DeepFace.analyze(
        #                 img_path=frame,
        #                 actions=["emotion"],
        #                 detector_backend=self.detector_backend,
        #             )
        #             if isinstance(res, list) and res:
        #                 res = res[0]
        #             if isinstance(res, dict) and "emotion" in res:
        #                 emo = self._sanitize(res.get("emotion", {}))
        #                 payload["emotion"] = emo
        #                 payload["has_face"] = True if emo else False
        #                 if emo:
        #                     payload["dominant_emotion"] = max(emo, key=emo.get)
        #         except Exception as e:
        #             # 推送失败不报错，只在控制台提示一次
        #             # print(f"[emotion_loop warn] {e}")
        #             pass
        while not self._emo_stop.is_set():
            t0 = time.time()
            payload = {
                "ts": t0,
                "has_face": False,
                "dominant_emotion": None,
                "emotion": {},
                "source": "FacePromptDetector",
            }
            frame = self.camera.read_latest_frame()
            if frame is not None:
                try:
                    res = DeepFace.analyze(
                        img_path=frame,
                        actions=["emotion"],
                        detector_backend=self.detector_backend,
                    )
                    if isinstance(res, list) and res:
                        res = res[0]
                    if isinstance(res, dict) and "emotion" in res:
                        emo = self._sanitize(res.get("emotion", {}))
                        payload["emotion"] = emo
                        payload["has_face"] = True if emo else False
                        if emo:
                            payload["dominant_emotion"] = max(emo, key=emo.get)
                            # ✅ 新增：情绪线程也视作“看到人脸”
                            self._mark_face_seen()
                except Exception:
                    pass
            try:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                sock.sendto(data, addr)
            except Exception as e:
                # 网络异常不要打断循环
                # print(f"[emotion_loop send warn] {e}")
                pass

            # 睡到下一个周期
            used = time.time() - t0
            remain = interval - used
            if remain < 0.0:
                remain = 0.0
            self._emo_stop.wait(remain)

    def start_emotion_stream(
        self,
        host: str = "127.0.0.1",
        port: int = 5555,
        interval_sec: float = 2.0,
    ):
        """
        启动持续情绪推送（UDP JSON）。
        可重复调用（已在运行则忽略）。
        """
        if self._emo_thread and self._emo_thread.is_alive():
            return
        # 确保相机已在运行（run() 会 start；如果你没调 run，也可以手动 camera.start() 后再启动）
        # 这里不强制 start，相机未启动时会推送 has_face=False 的心跳。

        self._emo_addr = (host, int(port))
        self._emo_interval = float(interval_sec)
        self._emo_stop.clear()
        self._emo_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._emo_thread = threading.Thread(target=self._emotion_loop, daemon=True)
        self._emo_thread.start()
        print(f"[FacePromptDetector] Emotion stream started udp://{host}:{port} @ {interval_sec}s")

    def stop_emotion_stream(self):
        """可选：停止持续情绪推送。"""
        self._emo_stop.set()
        if self._emo_thread:
            try:
                self._emo_thread.join(timeout=1.0)
            except Exception:
                pass
        self._emo_thread = None
        if self._emo_sock:
            try:
                self._emo_sock.close()
            except Exception:
                pass
        self._emo_sock = None
        print("[FacePromptDetector] Emotion stream stopped")

    # ---------------- 外部主入口（一次性获取 prompt） ----------------
    def run(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        启动相机与分析；若在 timeout 秒内连续命中 required_consecutive 次有人脸，返回 prompt；
        若超时或异常中断，返回 None。

        注意：不再在结束时关闭相机，以便后续持续情绪推送可以一直运行。
        """
        self._stop_event.clear()
        self._face_counter = 0
        self._prompt_result = None

        # 启动相机（如果还没起）
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
        # 不关闭相机： self.camera.stop()  # ← 移除
        return self._prompt_result