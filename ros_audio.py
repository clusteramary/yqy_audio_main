from typing import Optional

try:
    import rospy
    from std_msgs.msg import Bool, ByteMultiArray, String

    try:
        from audio_common_msgs.msg import AudioData as RosAudioData

        _HAS_AUDIO_DATA_MSG: bool = True
    except Exception:
        RosAudioData = None  # type: ignore
        _HAS_AUDIO_DATA_MSG = False
    _HAS_ROS1: bool = True
except Exception:
    rospy = None  # type: ignore
    Bool = None  # type: ignore
    ByteMultiArray = None  # type: ignore
    String = None  # type: ignore
    RosAudioData = None  # type: ignore
    _HAS_ROS1 = False
    _HAS_AUDIO_DATA_MSG = False

from duplex_audio import build_stop_message, pack_audio_frame


class Ros1SpeakerStream:
    def __init__(
        self,
        topic: str = "/robot/speaker/audio",
        node_name: str = "speaker_publisher",
        queue_size: int = 10,
        latched: bool = False,
        control_topic: str = "/audio/control",
        duplex_mode: str = "half",
        audio_frame_ms: int = 20,
        sample_rate: int = 24000,
        channels: int = 1,
        sample_width: int = 2,
    ):
        if not _HAS_ROS1:
            raise RuntimeError("Ros1SpeakerStreamNot in ROS1 (rospy)。Please run in ROS1 environment。")
        if not rospy.core.is_initialized():  # type: ignore[attr-defined]
            rospy.init_node(node_name, anonymous=True, disable_signals=True)  # type: ignore[attr-defined]
        self.topic = topic
        self.control_topic = control_topic
        self.duplex_mode = duplex_mode
        self.audio_frame_ms = audio_frame_ms
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        self._closed = False
        self._frame_seq = 0
        self._utterance_id = 0
        self._control_pub = None

        self._use_audio_msg = _HAS_AUDIO_DATA_MSG
        if self._use_audio_msg:
            self._pub = rospy.Publisher(  # type: ignore[attr-defined]
                topic, RosAudioData, queue_size=queue_size, latch=latched  # type: ignore[arg-type]
            )
        else:
            self._pub = rospy.Publisher(  # type: ignore[attr-defined]
                topic, ByteMultiArray, queue_size=queue_size, latch=latched  # type: ignore[arg-type]
            )

        # 控制通道始终可用：即使 half 模式，也允许在进程退出时发送 stop，清空下位机缓冲。
        self._control_pub = rospy.Publisher(  # type: ignore[attr-defined]
            control_topic,
            String,
            queue_size=10,
            latch=False,
        )

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

    def write_framed(
        self,
        utterance_id: int,
        chunk_seq: int,
        is_last: bool,
        audio_bytes: bytes,
    ):
        if self._closed:
            return
        framed = pack_audio_frame(utterance_id, chunk_seq, is_last, audio_bytes)
        if self._use_audio_msg:
            msg = RosAudioData()  # type: ignore[call-arg]
            msg.data = list(framed)
        else:
            msg = ByteMultiArray()  # type: ignore[call-arg]
            msg.data = list(framed)
        self._pub.publish(msg)

    def interrupt(self, utterance_id: int, reason: str = "barge_in"):
        if self._closed:
            return
        if self._control_pub is None:
            return
        stop_msg = build_stop_message(utterance_id, reason)
        self._control_pub.publish(String(data=stop_msg))

    def set_utterance_id(self, utterance_id: int):
        self._utterance_id = utterance_id
        self._frame_seq = 0

    def stop_stream(self):
        pass

    def close(self):
        self._closed = True
