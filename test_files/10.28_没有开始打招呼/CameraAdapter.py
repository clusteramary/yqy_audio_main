# face_prompt_detector_ros.py
import threading
import time
from typing import Optional

import cv2
import numpy as np

# ----- 可选依赖：RealSense / ROS1 / ROS2 -----
try:
    import pyrealsense2 as rs

    _HAS_REALSENSE = True
except Exception:
    _HAS_REALSENSE = False

try:
    import rospy
    from cv_bridge import CvBridge
    from sensor_msgs.msg import CompressedImage as RosCompressedImage
    from sensor_msgs.msg import Image as RosImage

    _HAS_ROS1 = True
except Exception:
    _HAS_ROS1 = False

try:
    import rclpy
    from cv_bridge import CvBridge as CvBridge2
    from rclpy.node import Node
    from sensor_msgs.msg import CompressedImage as Ros2CompressedImage
    from sensor_msgs.msg import Image as Ros2Image

    _HAS_ROS2 = True
except Exception:
    _HAS_ROS2 = False

from deepface import DeepFace


class CameraAdapter:
    """
    相机适配器：统一“启动采集/停止采集/读取最新帧”的接口。
    支持：
    - kind="realsense"  : 直接读 RealSense 彩色流
    - kind="opencv"     : 直接读 OpenCV 摄像头
    - kind="ros1"       : 从 ROS1 话题订阅图像
    - kind="ros2"       : 从 ROS2 话题订阅图像
    """

    def __init__(
        self,
        kind: str = "realsense",
        width: int = 640,
        height: int = 480,
        fps: int = 30,
        warmup_frames: int = 5,
        # OpenCV 专用
        opencv_index: int = 0,
        # ROS 专用
        ros_topic: str = "/camera/color/image_raw",
        ros_compressed: bool = False,  # True 则订阅 /compressed
        ros_queue_size: int = 5,
        ros_node_name: str = "camera_adapter",
        ros_namespace: Optional[str] = None,
    ):
        self.kind = kind.lower()
        self.width = width
        self.height = height
        self.fps = fps
        self.warmup_frames = warmup_frames
        self.opencv_index = opencv_index

        # ROS 参数
        self.ros_topic = ros_topic
        self.ros_compressed = ros_compressed
        self.ros_queue_size = ros_queue_size
        self.ros_node_name = ros_node_name
        self.ros_namespace = ros_namespace

        # 最近一帧
        self._last_frame = None
        self._last_frame_lock = threading.Lock()

        # 控制
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 运行期资源
        self._rs_pipeline = None
        self._rs_config = None
        self._cv_cap = None

        # ROS1
        self._ros1_bridge = None
        self._ros1_sub = None

        # ROS2
        self._ros2_bridge = None
        self._ros2_node = None
        self._ros2_sub = None

        # 基础依赖检查
        if self.kind == "realsense" and not _HAS_REALSENSE:
            raise RuntimeError("未安装 pyrealsense2，无法使用 kind='realsense'。")
        if self.kind == "ros1" and not _HAS_ROS1:
            raise RuntimeError("未检测到 ROS1 (rospy / sensor_msgs / cv_bridge)。")
        if self.kind == "ros2" and not _HAS_ROS2:
            raise RuntimeError("未检测到 ROS2 (rclpy / sensor_msgs / cv_bridge)。")

    # ----------- 公共API -----------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._release_resources()

    def read_latest_frame(self) -> Optional[np.ndarray]:
        with self._last_frame_lock:
            return None if self._last_frame is None else self._last_frame.copy()

    # ----------- 内部实现 -----------
    def _run(self):
        try:
            if self.kind == "realsense":
                self._run_realsense()
            elif self.kind == "opencv":
                self._run_opencv()
            elif self.kind == "ros1":
                self._run_ros1()
            elif self.kind == "ros2":
                self._run_ros2()
            else:
                raise ValueError(f"不支持的相机类型 kind={self.kind!r}")
        except Exception as e:
            print(f"[CameraAdapter ERROR] {e}")
        finally:
            self._release_resources()

    # --- RealSense ---
    def _run_realsense(self):
        self._rs_pipeline = rs.pipeline()
        self._rs_config = rs.config()
        self._rs_config.enable_stream(
            rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps
        )
        self._rs_pipeline.start(self._rs_config)

        for _ in range(self.warmup_frames):
            self._rs_pipeline.wait_for_frames()

        while not self._stop_event.is_set():
            frames = self._rs_pipeline.wait_for_frames()
            color_frame = frames.get_color_frame()
            if not color_frame:
                continue
            color = np.asanyarray(color_frame.get_data())
            with self._last_frame_lock:
                self._last_frame = color

    # --- OpenCV ---
    def _run_opencv(self):
        self._cv_cap = cv2.VideoCapture(self.opencv_index, cv2.CAP_DSHOW)
        self._cv_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self._cv_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self._cv_cap.set(cv2.CAP_PROP_FPS, self.fps)
        if not self._cv_cap.isOpened():
            raise RuntimeError(f"无法打开 OpenCV 摄像头 index={self.opencv_index}")

        for _ in range(self.warmup_frames):
            self._cv_cap.read()

        while not self._stop_event.is_set():
            ok, frame = self._cv_cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            if (frame.shape[1] != self.width) or (frame.shape[0] != self.height):
                frame = cv2.resize(frame, (self.width, self.height))
            with self._last_frame_lock:
                self._last_frame = frame

    # --- ROS1 ---
    def _ros1_image_cb(self, msg):
        try:
            img = self._ros1_bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception:
            # 回退：根据来源编码处理
            img = self._ros1_bridge.imgmsg_to_cv2(msg)  # 可能为 RGB
            if img.ndim == 3 and img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        self._update_frame(img)

    def _ros1_compressed_cb(self, msg):
        np_arr = np.frombuffer(msg.data, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)  # BGR
        self._update_frame(img)

    def _run_ros1(self):
        # 初始化节点（若外部已 init 就跳过）
        if not rospy.core.is_initialized():
            rospy.init_node(self.ros_node_name, anonymous=True, disable_signals=True)
        if self.ros_namespace:
            self.ros_topic = (
                f"{self.ros_namespace.rstrip('/')}/{self.ros_topic.lstrip('/')}"
            )

        self._ros1_bridge = CvBridge()
        if self.ros_compressed:
            topic = (
                self.ros_topic
                if self.ros_topic.endswith("/compressed")
                else (self.ros_topic.rstrip("/") + "/compressed")
            )
            self._ros1_sub = rospy.Subscriber(
                topic,
                RosCompressedImage,
                self._ros1_compressed_cb,
                queue_size=self.ros_queue_size,
            )
            print(f"[CameraAdapter] ROS1 订阅 CompressedImage: {topic}")
        else:
            self._ros1_sub = rospy.Subscriber(
                self.ros_topic,
                RosImage,
                self._ros1_image_cb,
                queue_size=self.ros_queue_size,
            )
            print(f"[CameraAdapter] ROS1 订阅 Image: {self.ros_topic}")

        # 不使用 rospy.spin()（难以优雅停止），用轻量循环保持进程与回调运行
        rate = rospy.Rate(200)
        while not self._stop_event.is_set() and not rospy.is_shutdown():
            rate.sleep()

    # --- ROS2 ---
    def _run_ros2(self):
        class _Node(Node):
            pass

        if not rclpy.ok():
            rclpy.init()

        node_name = self.ros_node_name
        self._ros2_node = _Node(node_name)
        self._ros2_bridge = CvBridge2()

        topic = self.ros_topic
        if self.ros_namespace:
            ns = self.ros_namespace.rstrip("/")
            topic = f"{ns}/{self.ros_topic.lstrip('/')}"

        if self.ros_compressed:
            # ROS2 压缩图像话题名称通常也是 ".../compressed"
            topic = (
                topic
                if topic.endswith("/compressed")
                else (topic.rstrip("/") + "/compressed")
            )
            self._ros2_sub = self._ros2_node.create_subscription(
                Ros2CompressedImage,
                topic,
                self._ros2_compressed_cb,
                self.ros_queue_size,
            )
            print(f"[CameraAdapter] ROS2 订阅 CompressedImage: {topic}")
        else:
            self._ros2_sub = self._ros2_node.create_subscription(
                Ros2Image, topic, self._ros2_image_cb, self.ros_queue_size
            )
            print(f"[CameraAdapter] ROS2 订阅 Image: {topic}")

        # 用独立线程 spin，便于停止
        executor = rclpy.executors.SingleThreadedExecutor()
        executor.add_node(self._ros2_node)

        def _spin():
            while not self._stop_event.is_set():
                executor.spin_once(timeout_sec=0.1)

        spin_thread = threading.Thread(target=_spin, daemon=True)
        spin_thread.start()

        # 保持到 stop
        while not self._stop_event.is_set():
            time.sleep(0.05)

        executor.remove_node(self._ros2_node)
        self._ros2_node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

    def _ros2_image_cb(self, msg):
        try:
            img = self._ros2_bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception:
            img = self._ros2_bridge.imgmsg_to_cv2(msg)
            if img.ndim == 3 and img.shape[2] == 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        self._update_frame(img)

    def _ros2_compressed_cb(self, msg):
        np_arr = np.frombuffer(msg.data, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        self._update_frame(img)

    # --- 共享 ---
    def _update_frame(self, img: np.ndarray):
        # 尺寸对齐（可选）
        if img is not None and (
            img.shape[1] != self.width or img.shape[0] != self.height
        ):
            img = cv2.resize(img, (self.width, self.height))
        with self._last_frame_lock:
            self._last_frame = img

    def _release_resources(self):
        # OpenCV
        if self._cv_cap is not None:
            try:
                self._cv_cap.release()
            except Exception:
                pass
            self._cv_cap = None

        # RealSense
        if self._rs_pipeline is not None:
            try:
                self._rs_pipeline.stop()
            except Exception:
                pass
            self._rs_pipeline = None

        # ROS1
        if self._ros1_sub is not None:
            try:
                self._ros1_sub.unregister()
            except Exception:
                pass
            self._ros1_sub = None

        # ROS2
        self._ros2_sub = None  # 已在 _run_ros2 中处理节点销毁/关停

        print("[CameraAdapter] 采集资源已释放。")
