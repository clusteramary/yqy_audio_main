#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ros_audio_sink.py

import json
import queue
import signal
import threading
import time

import rospy
from std_msgs.msg import Bool, String, ByteMultiArray  # 添加 Bool, String 消息类型

# 尝试使用 audio_common_msgs/AudioData（如未安装会回退到 ByteMultiArray）
try:
    from audio_common_msgs.msg import AudioData as RosAudioData
    HAS_AUDIO_DATA = True
except Exception:
    HAS_AUDIO_DATA = False

import pyaudio


class AudioPlayer:
    """
    用 PyAudio 实时播放：收到一包就写一包，队列满则丢旧包以追帧。
    添加播放状态发布功能。
    支持 stop 控制消息中断播放。
    """

    def __init__(self, sample_rate=24000, channels=1, sample_format="s16le", device_index=None,
                 max_queue_packets=200, status_topic="/audio_playing_status"):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.sample_format = sample_format.lower()
        self.device_index = device_index
        self._q = queue.Queue(maxsize=max_queue_packets)
        self._stop = threading.Event()
        self._interrupted_utterance: str = ""

        # 添加播放状态发布
        self.status_pub = rospy.Publisher(status_topic, Bool, queue_size=10)
        self._last_status = False
        self._status_publish_interval = 0.1  # 状态发布间隔（秒）
        self._last_status_publish_time = 0

        self._pa = pyaudio.PyAudio()
        self._stream_kwargs = self._build_stream_kwargs()
        self._stream = self._pa.open(**self._stream_kwargs)

        self._lock = threading.Lock()
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        rospy.loginfo("[AudioPlayer] opened: rate=%d, ch=%d, fmt=%s, dev=%s",
                      self.sample_rate, self.channels, self.sample_format, str(self.device_index))

    def _build_stream_kwargs(self):
        if self.sample_format == "s16le":
            pa_fmt = pyaudio.paInt16
        elif self.sample_format == "f32le":
            pa_fmt = pyaudio.paFloat32
        else:
            rospy.logwarn("未知 sample_format=%s，回退到 s16le", self.sample_format)
            pa_fmt = pyaudio.paInt16
        kwargs = dict(format=pa_fmt, channels=self.channels, rate=self.sample_rate, output=True)
        if self.device_index not in (None, ""):
            kwargs["output_device_index"] = int(self.device_index)
        return kwargs

    def _reopen_stream(self):
        """Close and reopen PyAudio stream to flush buffers."""
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        self._stream = self._pa.open(**self._stream_kwargs)

    def _publish_status(self, is_playing):
        """发布播放状态，避免过于频繁的发布"""
        current_time = time.time()
        if is_playing != self._last_status or current_time - self._last_status_publish_time > self._status_publish_interval:
            try:
                self.status_pub.publish(Bool(data=is_playing))
                self._last_status = is_playing
                self._last_status_publish_time = current_time
            except Exception as e:
                rospy.logwarn("发布播放状态失败: %s", e)

    def _loop(self):
        last_warn = 0.0
        is_playing = False

        while not self._stop.is_set():
            try:
                pkt = self._q.get(timeout=0.5)
                is_playing = True
                self._publish_status(True)
            except queue.Empty:
                is_playing = False
                self._publish_status(False)
                continue

            # Check if this utterance was interrupted
            if self._interrupted_utterance:
                continue

            try:
                with self._lock:
                    self._stream.write(pkt)
            except Exception as e:
                if time.time() - last_warn > 2.0:
                    rospy.logwarn("播放失败（设备忙/断开?）：%s", e)
                    last_warn = time.time()
                time.sleep(0.01)

        # 循环结束时发布停止状态
        self._publish_status(False)

    def push(self, audio_bytes: bytes):
        if not audio_bytes:
            return
        # Drop audio if interrupted
        if self._interrupted_utterance:
            return
        try:
            self._q.put_nowait(audio_bytes)
        except queue.Full:
            # 丢弃最旧包以保持实时
            try:
                _ = self._q.get_nowait()
            except Exception:
                pass
            try:
                self._q.put_nowait(audio_bytes)
            except Exception:
                pass

    def handle_stop(self, utterance_id: str = "", reason: str = ""):
        """Handle stop control message: clear queue and reset stream."""
        rospy.loginfo("[AudioPlayer] 收到 stop 控制, utterance=%s, reason=%s", utterance_id, reason)
        self._interrupted_utterance = utterance_id or "__all__"

        # Clear queue
        cleared = 0
        while not self._q.empty():
            try:
                self._q.get_nowait()
                cleared += 1
            except queue.Empty:
                break
        rospy.loginfo("[AudioPlayer] 清空队列, 丢弃 %d 包", cleared)

        # Reset stream to flush hardware buffers
        with self._lock:
            self._reopen_stream()

        self._publish_status(False)

    def clear_interrupt(self):
        """Clear interrupted state so new audio can be accepted."""
        self._interrupted_utterance = ""

    def close(self):
        self._stop.set()
        try:
            self._th.join(timeout=1.0)
        except Exception:
            pass
        try:
            self._stream.stop_stream()
            self._stream.close()
        except Exception:
            pass
        try:
            self._pa.terminate()
        except Exception:
            pass
        self._publish_status(False)
        rospy.loginfo("[AudioPlayer] closed")


class LocalAudioSink:
    def __init__(self, topic="/audio", sample_rate=24000, channels=1,
                 sample_format="s16le", device_index=None,
                 sub_type="auto", max_queue_packets=200,
                 status_topic="/audio_playing_status",
                 control_topic="/audio/control"):
        self.player = AudioPlayer(
            sample_rate=sample_rate,
            channels=channels,
            sample_format=sample_format,
            device_index=device_index,
            max_queue_packets=max_queue_packets,
            status_topic=status_topic
        )

        # sub_type: "auto" | "audio" | "bytes"
        self.sub_aud = None
        self.sub_bytes = None
        self.sub_control = None

        if sub_type == "audio":
            if not HAS_AUDIO_DATA:
                rospy.logerr("要求订 AudioData，但本机无 audio_common_msgs。")
            else:
                self.sub_aud = rospy.Subscriber(topic, RosAudioData, self._cb_audio, queue_size=50)
                rospy.loginfo("订阅 AudioData: %s", topic)

        elif sub_type == "bytes":
            self.sub_bytes = rospy.Subscriber(topic, ByteMultiArray, self._cb_bytes, queue_size=50)
            rospy.loginfo("订阅 ByteMultiArray: %s", topic)

        else:  # auto
            if HAS_AUDIO_DATA:
                self.sub_aud = rospy.Subscriber(topic, RosAudioData, self._cb_audio, queue_size=50)
                rospy.loginfo("订阅 AudioData: %s", topic)
            else:
                rospy.logwarn("audio_common_msgs/AudioData 不可用，改用 ByteMultiArray。")
                self.sub_bytes = rospy.Subscriber(topic, ByteMultiArray, self._cb_bytes, queue_size=50)

        # Subscribe to control topic for stop commands
        if control_topic:
            self.sub_control = rospy.Subscriber(
                control_topic, String, self._cb_control, queue_size=10
            )
            rospy.loginfo("订阅控制话题: %s", control_topic)

    def _cb_audio(self, msg):
        try:
            data = msg.data
            b = bytes(data) if isinstance(data, (bytes, bytearray)) else bytes(bytearray(data))
            self.player.push(b)
        except Exception as e:
            rospy.logwarn("AudioData 解析失败: %s", e)

    def _cb_bytes(self, msg):
        try:
            data = msg.data
            b = bytes(data) if isinstance(data, (bytes, bytearray)) else bytes(bytearray(data))
            self.player.push(b)
        except Exception as e:
            rospy.logwarn("ByteMultiArray 解析失败: %s", e)

    def _cb_control(self, msg):
        """Handle control messages from /audio/control topic."""
        try:
            ctrl = json.loads(msg.data)
            command = ctrl.get("command", "")
            if command == "stop":
                utterance_id = ctrl.get("utterance_id", "")
                reason = ctrl.get("reason", "")
                self.player.handle_stop(utterance_id, reason)
            elif command == "resume":
                self.player.clear_interrupt()
                rospy.loginfo("[AudioSink] 收到 resume 控制")
            else:
                rospy.logwarn("[AudioSink] 未知控制命令: %s", command)
        except json.JSONDecodeError:
            rospy.logwarn("[AudioSink] 控制消息 JSON 解析失败: %s", msg.data)
        except Exception as e:
            rospy.logwarn("[AudioSink] 处理控制消息失败: %s", e)

    def close(self):
        self.player.close()


def main():
    rospy.init_node("local_audio_sink", anonymous=True)

    topic = rospy.get_param("~topic", "/audio")                  # 你的发布话题
    rate = int(rospy.get_param("~sample_rate", 24000))           # 与发送端一致
    ch = int(rospy.get_param("~channels", 1))
    fmt = rospy.get_param("~sample_format", "f32le")             # 's16le' 或 'f32le'
    dev = rospy.get_param("~device_index", None)                 # 可选，输出声卡索引
    status_topic = rospy.get_param("~status_topic", "/audio_playing_status")  # 状态话题参数
    control_topic = rospy.get_param("~control_topic", "/audio/control")  # 控制话题参数

    rospy.loginfo("LocalAudioSink: topic=%s, rate=%d, ch=%d, fmt=%s, dev=%s, status_topic=%s, control_topic=%s",
                  topic, rate, ch, fmt, str(dev), status_topic, control_topic)

    # main() 里加读取 sub_type 参数：
    sub_type = rospy.get_param("~sub_type", "auto")  # "auto"|"audio"|"bytes"
    sink = LocalAudioSink(
        topic=topic,
        sample_rate=rate,
        channels=ch,
        sample_format=fmt,
        device_index=dev,
        sub_type=sub_type,
        status_topic=status_topic,
        control_topic=control_topic,
    )

    # Register signal handler for clean shutdown
    def _shutdown_handler(sig, frame):
        rospy.loginfo("[AudioSink] 收到中断信号, 停止播放并清理...")
        sink.player.handle_stop(reason="shutdown")
        sink.close()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _shutdown_handler)

    try:
        rospy.on_shutdown(lambda: (
            sink.player.handle_stop(reason="ros_shutdown"),
            sink.close(),
        ))
        rospy.spin()
    finally:
        sink.close()


if __name__ == "__main__":
    main()
