#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import queue
import threading
import time

import rospy
from std_msgs.msg import ByteMultiArray

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
    """

    def __init__(self, sample_rate=24000, channels=1, sample_format="s16le", device_index=None,
                 max_queue_packets=200):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.sample_format = sample_format.lower()
        self.device_index = device_index
        self._q = queue.Queue(maxsize=max_queue_packets)
        self._stop = threading.Event()

        self._pa = pyaudio.PyAudio()
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
        self._stream = self._pa.open(**kwargs)

        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        rospy.loginfo("[AudioPlayer] opened: rate=%d, ch=%d, fmt=%s, dev=%s",
                      self.sample_rate, self.channels, self.sample_format, str(self.device_index))

    def _loop(self):
        last_warn = 0.0
        while not self._stop.is_set():
            try:
                pkt = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._stream.write(pkt)
            except Exception as e:
                if time.time() - last_warn > 2.0:
                    rospy.logwarn("播放失败（设备忙/断开?）：%s", e)
                    last_warn = time.time()
                time.sleep(0.01)

    def push(self, audio_bytes: bytes):
        if not audio_bytes:
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
        rospy.loginfo("[AudioPlayer] closed")


class LocalAudioSink:
    # 片段：替换 LocalAudioSink.__init__

    def __init__(self, topic="/audio", sample_rate=24000, channels=1,
                 sample_format="s16le", device_index=None,
                 sub_type="auto", max_queue_packets=200):
        self.player = AudioPlayer(sample_rate=sample_rate, channels=channels,
                                  sample_format=sample_format, device_index=device_index,
                                  max_queue_packets=max_queue_packets)

        # sub_type: "auto" | "audio" | "bytes"
        self.sub_aud = None
        self.sub_bytes = None

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

    def close(self):
        self.player.close()


def main():
    rospy.init_node("local_audio_sink", anonymous=True)
    

    topic = rospy.get_param("~topic", "/audio")                  # 你的发布话题
    rate = int(rospy.get_param("~sample_rate", 24000))           # 与发送端一致
    ch = int(rospy.get_param("~channels", 1))
    fmt = rospy.get_param("~sample_format", "f32le")             # 's16le' 或 'f32le'
    dev = rospy.get_param("~device_index", None)                 # 可选，输出声卡索引

    rospy.loginfo("LocalAudioSink: topic=%s, rate=%d, ch=%d, fmt=%s, dev=%s",
                  topic, rate, ch, fmt, str(dev))
    
    # main() 里加读取 sub_type 参数：
    sub_type = rospy.get_param("~sub_type", "auto")  # "auto"|"audio"|"bytes"
    sink = LocalAudioSink(topic=topic, sample_rate=rate, channels=ch,
                      sample_format=fmt, device_index=dev, sub_type=sub_type)

    try:
        rospy.spin()
    finally:
        sink.close()


if __name__ == "__main__":
    main()