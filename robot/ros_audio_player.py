#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import queue
import threading
import time

import pyaudio
import rospy
from std_msgs.msg import Bool, ByteMultiArray, String

from duplex_audio import parse_control_message, try_unpack_audio_frame

try:
    from audio_common_msgs.msg import AudioData as RosAudioData

    HAS_AUDIO_DATA = True
except Exception:
    RosAudioData = None
    HAS_AUDIO_DATA = False


class AudioPlayer:
    """Interruptible local audio player for ROS speaker data."""

    def __init__(
        self,
        sample_rate=24000,
        channels=1,
        sample_format="s16le",
        device_index=None,
        max_queue_packets=200,
        status_topic="/audio_playing_status",
    ):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.sample_format = str(sample_format).lower()
        self.device_index = device_index
        self._q = queue.Queue(maxsize=max_queue_packets)
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self.status_pub = rospy.Publisher(status_topic, Bool, queue_size=10)
        self._last_status = False
        self._status_publish_interval = 0.1
        self._last_status_publish_time = 0.0

        self._cancelled_utterances = set()
        self._latest_utterance_id = None
        self._active_utterance_id = None

        self._pa = pyaudio.PyAudio()
        self._pa_format = self._resolve_format(self.sample_format)
        self._stream = None
        self._open_stream_locked()

        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        rospy.loginfo(
            "[AudioPlayer] opened: rate=%d, ch=%d, fmt=%s, dev=%s",
            self.sample_rate,
            self.channels,
            self.sample_format,
            str(self.device_index),
        )

    def _resolve_format(self, sample_format):
        if sample_format == "s16le":
            return pyaudio.paInt16
        if sample_format == "f32le":
            return pyaudio.paFloat32
        rospy.logwarn("Unknown sample_format=%s, falling back to s16le", sample_format)
        return pyaudio.paInt16

    def _open_stream_locked(self):
        if self._stream is not None:
            return

        kwargs = dict(
            format=self._pa_format,
            channels=self.channels,
            rate=self.sample_rate,
            output=True,
        )
        if self.device_index not in (None, ""):
            kwargs["output_device_index"] = int(self.device_index)
        self._stream = self._pa.open(**kwargs)

    def _publish_status(self, is_playing):
        current_time = time.time()
        if (
            is_playing != self._last_status
            or current_time - self._last_status_publish_time > self._status_publish_interval
        ):
            try:
                self.status_pub.publish(Bool(data=is_playing))
                self._last_status = is_playing
                self._last_status_publish_time = current_time
            except Exception as e:
                rospy.logwarn("Failed to publish playback status: %s", e)

    def _clear_queue_locked(self):
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def _hard_reset_stream_locked(self):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
        except Exception:
            pass

        try:
            if self._stream is not None:
                self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None

        if not self._stop.is_set():
            try:
                self._open_stream_locked()
            except Exception as e:
                rospy.logwarn("Failed to reopen audio stream: %s", e)

    def _prune_cancelled_locked(self):
        if self._latest_utterance_id is None:
            return
        floor = max(0, int(self._latest_utterance_id) - 32)
        self._cancelled_utterances = {
            utterance_id
            for utterance_id in self._cancelled_utterances
            if utterance_id >= floor
        }

    def _should_drop_locked(self, utterance_id):
        if utterance_id is None:
            return False
        if utterance_id in self._cancelled_utterances:
            return True
        if self._latest_utterance_id is not None and utterance_id < self._latest_utterance_id:
            return True
        return False

    def _loop(self):
        last_warn = 0.0
        while not self._stop.is_set():
            try:
                utterance_id, pkt = self._q.get(timeout=0.5)
            except queue.Empty:
                self._publish_status(False)
                continue

            with self._lock:
                if self._should_drop_locked(utterance_id):
                    continue
                if utterance_id is not None:
                    self._active_utterance_id = utterance_id
                try:
                    self._open_stream_locked()
                except Exception as e:
                    rospy.logwarn("Failed to open audio stream: %s", e)
                    continue

            self._publish_status(True)
            try:
                with self._lock:
                    if self._should_drop_locked(utterance_id):
                        continue
                    self._stream.write(pkt)
            except Exception as e:
                if time.time() - last_warn > 2.0:
                    rospy.logwarn("Playback failed: %s", e)
                    last_warn = time.time()
                time.sleep(0.01)

        self._publish_status(False)

    def push(self, audio_bytes: bytes, utterance_id=None):
        if not audio_bytes:
            return

        with self._lock:
            if utterance_id is not None:
                utterance_id = int(utterance_id)
                if self._should_drop_locked(utterance_id):
                    return
                if self._latest_utterance_id is None or utterance_id > self._latest_utterance_id:
                    self._latest_utterance_id = utterance_id
                    self._prune_cancelled_locked()

        try:
            self._q.put_nowait((utterance_id, audio_bytes))
        except queue.Full:
            try:
                _ = self._q.get_nowait()
            except Exception:
                pass
            try:
                self._q.put_nowait((utterance_id, audio_bytes))
            except Exception:
                pass

    def cancel_utterance(self, utterance_id: int, reason: str = "barge_in"):
        utterance_id = int(utterance_id)
        with self._lock:
            self._cancelled_utterances.add(utterance_id)
            if self._latest_utterance_id is None or utterance_id > self._latest_utterance_id:
                self._latest_utterance_id = utterance_id
            self._prune_cancelled_locked()
            self._clear_queue_locked()
            self._hard_reset_stream_locked()
            if self._active_utterance_id == utterance_id:
                self._active_utterance_id = None
        self._publish_status(False)
        rospy.loginfo(
            "[AudioPlayer] cancelled utterance %s (%s)",
            utterance_id,
            reason,
        )

    def close(self):
        self._stop.set()
        try:
            self._th.join(timeout=1.0)
        except Exception:
            pass
        with self._lock:
            self._hard_reset_stream_locked()
        try:
            self._pa.terminate()
        except Exception:
            pass
        rospy.loginfo("[AudioPlayer] closed")


class LocalAudioSink:
    def __init__(
        self,
        topic="/audio",
        control_topic="/audio/control",
        sample_rate=24000,
        channels=1,
        sample_format="s16le",
        device_index=None,
        sub_type="auto",
        max_queue_packets=200,
        status_topic="/audio_playing_status",
    ):
        self.player = AudioPlayer(
            sample_rate=sample_rate,
            channels=channels,
            sample_format=sample_format,
            device_index=device_index,
            max_queue_packets=max_queue_packets,
            status_topic=status_topic,
        )

        self.sub_aud = None
        self.sub_bytes = None
        self.sub_control = rospy.Subscriber(
            control_topic,
            String,
            self._cb_control,
            queue_size=10,
            tcp_nodelay=True,
        )

        if sub_type == "audio":
            if not HAS_AUDIO_DATA:
                rospy.logerr("audio_common_msgs/AudioData is unavailable")
            else:
                self.sub_aud = rospy.Subscriber(
                    topic,
                    RosAudioData,
                    self._cb_audio,
                    queue_size=50,
                    tcp_nodelay=True,
                )
                rospy.loginfo("Subscribed to AudioData: %s", topic)
        elif sub_type == "bytes":
            self.sub_bytes = rospy.Subscriber(
                topic,
                ByteMultiArray,
                self._cb_bytes,
                queue_size=50,
                tcp_nodelay=True,
            )
            rospy.loginfo("Subscribed to ByteMultiArray: %s", topic)
        else:
            if HAS_AUDIO_DATA:
                self.sub_aud = rospy.Subscriber(
                    topic,
                    RosAudioData,
                    self._cb_audio,
                    queue_size=50,
                    tcp_nodelay=True,
                )
                rospy.loginfo("Subscribed to AudioData: %s", topic)
            else:
                rospy.logwarn("AudioData unavailable, falling back to ByteMultiArray")
                self.sub_bytes = rospy.Subscriber(
                    topic,
                    ByteMultiArray,
                    self._cb_bytes,
                    queue_size=50,
                    tcp_nodelay=True,
                )

    def _handle_audio_bytes(self, data: bytes):
        frame = try_unpack_audio_frame(data)
        if frame is None:
            self.player.push(data)
            return

        self.player.push(frame.payload, utterance_id=frame.utterance_id)

    def _cb_audio(self, msg):
        try:
            data = msg.data
            payload = (
                bytes(data)
                if isinstance(data, (bytes, bytearray))
                else bytes(bytearray(data))
            )
            self._handle_audio_bytes(payload)
        except Exception as e:
            rospy.logwarn("Failed to parse AudioData: %s", e)

    def _cb_bytes(self, msg):
        try:
            data = msg.data
            payload = (
                bytes(data)
                if isinstance(data, (bytes, bytearray))
                else bytes(bytearray(data))
            )
            self._handle_audio_bytes(payload)
        except Exception as e:
            rospy.logwarn("Failed to parse ByteMultiArray: %s", e)

    def _cb_control(self, msg):
        try:
            control = parse_control_message(msg.data)
        except Exception:
            control = None

        if not control or control.cmd != "stop":
            return

        self.player.cancel_utterance(control.utterance_id, reason=control.reason)

    def close(self):
        self.player.close()


def main():
    rospy.init_node("local_audio_sink", anonymous=True)

    topic = rospy.get_param("~topic", "/audio")
    control_topic = rospy.get_param("~control_topic", "/audio/control")
    rate = int(rospy.get_param("~sample_rate", 24000))
    ch = int(rospy.get_param("~channels", 1))
    fmt = rospy.get_param("~sample_format", "f32le")
    dev = rospy.get_param("~device_index", None)
    status_topic = rospy.get_param("~status_topic", "/audio_playing_status")
    sub_type = rospy.get_param("~sub_type", "auto")

    rospy.loginfo(
        "LocalAudioSink: topic=%s, control=%s, rate=%d, ch=%d, fmt=%s, dev=%s, status=%s",
        topic,
        control_topic,
        rate,
        ch,
        fmt,
        str(dev),
        status_topic,
    )

    sink = LocalAudioSink(
        topic=topic,
        control_topic=control_topic,
        sample_rate=rate,
        channels=ch,
        sample_format=fmt,
        device_index=dev,
        sub_type=sub_type,
        status_topic=status_topic,
    )

    try:
        rospy.spin()
    finally:
        sink.close()


if __name__ == "__main__":
    main()
