from typing import Optional

try:
    import rospy
    from std_msgs.msg import Bool, ByteMultiArray

    try:
        from audio_common_msgs.msg import AudioData as RosAudioData

        _HAS_AUDIO_DATA_MSG: bool = True
    except Exception:
        RosAudioData = None  # type: ignore
        _HAS_AUDIO_DATA_MSG = False
    _HAS_ROS1: bool = True
except Exception:
    # 未安装 ROS1 时的兜底占位
    rospy = None  # type: ignore
    Bool = None  # type: ignore
    ByteMultiArray = None  # type: ignore
    RosAudioData = None  # type: ignore
    _HAS_ROS1 = False
    _HAS_AUDIO_DATA_MSG = False


class Ros1SpeakerStream:
    def __init__(
        self,
        topic: str = "/robot/speaker/audio",
        node_name: str = "speaker_publisher",
        queue_size: int = 10,
        latched: bool = False,
    ):
        if not _HAS_ROS1:
            raise RuntimeError("未检测到 ROS1 (rospy)。请在 ROS1 环境运行。")
        if not rospy.core.is_initialized():  # type: ignore[attr-defined]
            rospy.init_node(node_name, anonymous=True, disable_signals=True)  # type: ignore[attr-defined]
        self.topic = topic
        self._use_audio_msg = _HAS_AUDIO_DATA_MSG
        if self._use_audio_msg:
            self._pub = rospy.Publisher(  # type: ignore[attr-defined]
                topic, RosAudioData, queue_size=queue_size, latch=latched  # type: ignore[arg-type]
            )
        else:
            self._pub = rospy.Publisher(  # type: ignore[attr-defined]
                topic, ByteMultiArray, queue_size=queue_size, latch=latched  # type: ignore[arg-type]
            )
        self._closed = False

    def write(self, audio_bytes: bytes):
        if self._closed:
            return
        if self._use_audio_msg:
            msg = RosAudioData()  # type: ignore[call-arg]
            msg.data = list(audio_bytes)
        else:
            msg = ByteMultiArray()  # type: ignore[call-arg]
            msg.data = list(audio_bytes)
        self._pub.publish(msg)

    def stop_stream(self):
        pass

    def close(self):
        self._closed = True
