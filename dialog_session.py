from __future__ import annotations

import asyncio
import audioop
import json
import os
import queue
import random
import re
import signal
import socket
import struct
import threading
import time
import uuid
import wave
from typing import Any, Dict, Optional, Set

import pyaudio

import config

# AudioConfig 用于 AudioDeviceManager
from audio_constants import (
    ACTION_INDEX_BY_KEYWORD,
    ASR_KWS_PATTERNS,
    KWS_PRIORITY,
    LLM_KWS_PATTERNS,
    REPEAT_ACTION_COUNT,
    TARGET_CHANNELS,
    TARGET_CHUNK_SAMPLES,
    TARGET_SAMPLE_RATE,
    TARGET_SAMPLE_WIDTH,
    AudioConfig,
)
from audio_device_manager import AudioDeviceManager
from realtime_dialog_client import RealtimeDialogClient

# ---------- async to_thread 兼容 ----------
try:
    _to_thread = asyncio.to_thread  # Python 3.9+
except AttributeError:
    import functools

    async def _to_thread(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(func, *args, **kwargs)
        )


# ---------- ROS 相关 ----------
try:
    import rospy
    from std_msgs.msg import Bool, ByteMultiArray, Int32

    try:
        from audio_common_msgs.msg import AudioData as RosAudioData

        _HAS_AUDIO_DATA_MSG = True
    except Exception:
        RosAudioData = None
        _HAS_AUDIO_DATA_MSG = False
    _HAS_ROS1 = True
except Exception:
    rospy = None
    Bool = None
    ByteMultiArray = None
    Int32 = None
    RosAudioData = None
    _HAS_ROS1 = False
    _HAS_AUDIO_DATA_MSG = False

# # ---------- 音频处理常量 ----------
# TARGET_SAMPLE_RATE = 16000
# TARGET_SAMPLE_WIDTH = 2
# TARGET_CHANNELS = 1
# TARGET_CHUNK_SAMPLES = 320  # 16k * 20ms = 320 样本 → 每帧约 20ms

# # ---------- ASR / LLM 关键词配置 ----------
# # ASR 关键词配置：标签 -> 若干"包含匹配"的短语（用于语音识别结果）
# ASR_KWS_PATTERNS: Dict[str, list] = {
#     "wave": ["挥手", "挥一挥", "挥一下", "wave"],
#     "nod": ["点头", "点一下", "nod"],
#     "shake": ["击掌", "击一下"],
#     "woshou": ["握手", "握一下", "握个手", "shake"],
#     "end": ["再见", "拜拜", "bye"],
# }

# # LLM 文本关键词配置：标签 -> 若干"包含匹配"的短语（用于大模型文本 content）
# LLM_KWS_PATTERNS: Dict[str, list] = {
#     "left": ["向左转", "左"],
#     "right": ["右", "测试成功啦"],
#     # 结束访谈/结束控制，由 LLM 说出
#     "end": ["感谢你", "感谢您", "感谢"],
# }


def save_input_pcm_to_wav(pcm_data: bytes, filename: str) -> None:
    in_cfg = config.get_input_audio_config()
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(in_cfg["channels"])
        wf.setsampwidth(2)
        wf.setframerate(in_cfg["sample_rate"])
        wf.writeframes(pcm_data)


def save_output_to_file(audio_data: bytes, filename: str) -> None:
    if not audio_data:
        print("No audio data to save.")
        return
    try:
        with open(filename, "wb") as f:
            f.write(audio_data)
    except IOError as e:
        print(f"Failed to save pcm file: {e}")


class DialogSession:
    """
    负责：
      - 管理和大模型的 WebSocket 会话
      - 管理音频的输入（现在走 ROS /audio/audio）和输出（PyAudio / ROS1 speaker）
      - 处理 ctrl.txt + SAUC 队列识别
      - 处理 LLM / ASR 关键词，走 ROS 动作 index 话题
    """

    is_audio_file_input: bool

    def __init__(
        self,
        ws_config: Dict[str, Any],
        start_prompt: str,
        output_audio_format: str = "pcm",
        audio_file_path: str = "",
        duplex_mode: str = "half",
    ):
        self.start_prompt = start_prompt
        self.audio_file_path = audio_file_path
        self.is_audio_file_input = self.audio_file_path != ""
        if self.is_audio_file_input:
            self.quit_event = asyncio.Event()
        else:
            self.say_hello_over_event = asyncio.Event()

        self.session_id = str(uuid.uuid4())
        self.client = RealtimeDialogClient(
            config=ws_config,
            session_id=self.session_id,
            output_audio_format=output_audio_format,
        )
        if output_audio_format == "pcm_s16le":
            config.output_audio_config["format"] = "pcm_s16le"
            config.output_audio_config["bit_size"] = pyaudio.paInt16

        self._last_promote_ts = 0
        self.promote_task = asyncio.create_task(self._promote_task())
        self._promote_playing = False

        self.is_running = True
        self.is_session_finished = False
        self.is_user_querying = False
        self.is_sending_chat_tts_text = False
        self.audio_buffer = b""
        self._ratecv_state = None

        self._last_play_ts = 0.0
        self._duplex_mode = duplex_mode
        self.block_mic_while_playing = (duplex_mode == "half")
        config.output_audio_config["duplex_mode"] = duplex_mode
        config.output_audio_config["ros1_control_topic"] = getattr(
            config, "ROS_AUDIO_CONTROL_TOPIC", "/audio/control"
        )
        config.output_audio_config["ros1_audio_frame_ms"] = getattr(
            config, "ROS_AUDIO_FRAME_MS", 20
        )

        # ---------- 全双工 barge-in 跟踪 ----------
        self._bot_utterance_id = int(time.time() * 1000) & 0x7FFFFFFF
        self._last_sent_utterance_id = None
        self._utterance_chunk_seq = 0
        self._barge_in_counter = 0
        self._barge_in_start_ts = 0.0

        self._last_silence_ts = 0.0
        self._silence_interval_sec = 0.20
        self._half_duplex_mic_paused = False
        self._half_duplex_resume_after = 0.0
        self._half_duplex_resume_delay_sec = (
            getattr(config, "HALF_DUPLEX_RESUME_DELAY_MS", 250) / 1000.0
        )

        self.external_stop_event: Optional[asyncio.Event] = None

        # ---------- 用户活动时间戳（供视觉迎宾冷却判断） ----------
        self.last_user_activity_ts: float = time.time()

        # ---------- 下位机播放状态 ----------
        self.remote_playing = False
        self.remote_status_topic = "/audio_playing_status"
        self.remote_status_sub = None

        # ---------- ROS 动作 index 话题 & UDP MIC 指令 ----------
        self.action_index_topic = getattr(config, "ACTION_INDEX_TOPIC", "/action_index")
        self.action_index_pub = None
        self._init_action_index_publisher()

        self.mic_udp_host = "127.0.0.1"
        self.mic_udp_port = 5558
        self.mic_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # 关键词"挥手" 及去抖（当前 _wave_re 未直接使用，只保留字段）
        self._wave_re = re.compile(r"(挥手|揮手|招手|挥个手|挥下手)")
        self._kws_wave_cooldown = 1.5
        self._kws_wave_last_ts = 0.0

        # MIC 指令冷却
        self._last_mic_send_time = 0.0

        # ---------- LLM 输出关键短语检测缓冲 ----------
        self._llm_keyword_buffer: str = ""
        self._llm_buffer_max_len: int = 50  # 只保留最近若干字符即可
        # 当前这一轮 LLM 回复中已经触发过的关键词，避免同一轮触发多次
        self._llm_kws_fired: Set[str] = set()

        # ---------- 对话文本异步写入 ----------
        # 将 LLM 回复内容逐行写入 dialog.txt，使用异步队列与写入任务避免阻塞
        self.dialog_write_queue: asyncio.Queue = asyncio.Queue()
        self.dialog_file_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "dialog.txt"
        )
        # 创建写入任务（懒启动，连接建立后进行）
        self.dialog_writer_task: Optional[asyncio.Task] = None

        # 机器人与用户整段文本的累积/去重
        self._llm_text_accum: list[str] = []  # 累积机器人整段文本
        self._last_user_text_written: str = ""  # 去重：用户
        self._last_bot_text_written: str = ""  # 去重：机器人
        # 用户一轮话语的累积与写入控制
        self._user_text_accum: str = ""
        self._user_text_round_written: bool = False

        # ---------- ROS 下位机播放状态 + ROS 麦克风输入 ----------
        self.ros_audio_queue: Optional["queue.Queue[bytes]"] = None
        self.ros_audio_sub = None
        self.input_stream = None  # PyAudio 直连麦克风时的输入流

        input_mode = getattr(config, "INPUT_AUDIO_MODE", "ros1")
        input_cfg = config.get_input_audio_config()

        if not self.is_audio_file_input:
            if _HAS_ROS1:
                if not rospy.core.is_initialized():
                    rospy.init_node(
                        "audio_manager_client", anonymous=True, disable_signals=True
                    )
                self.remote_status_sub = rospy.Subscriber(
                    self.remote_status_topic,
                    Bool,
                    self._remote_audio_status_callback,
                    queue_size=10,
                )
                print(f"已订阅下位机播放状态话题: {self.remote_status_topic}")

            if input_mode == "ros1" and _HAS_ROS1:
                # === 订阅麦克风音频（别人已经用 audio_capture 打开设备并发布到 /audio/audio） ===
                if _HAS_AUDIO_DATA_MSG and RosAudioData is not None:
                    self.ros_audio_queue = queue.Queue(maxsize=50)
                    self.ros_audio_sub = rospy.Subscriber(
                        "/audio/audio",
                        RosAudioData,
                        self._ros_audio_callback,
                        queue_size=10,
                    )
                    print("已订阅麦克风音频话题: /audio/audio")
                else:
                    print(
                        "[ROS-MIC] 未检测到 audio_common_msgs/AudioData，无法订阅麦克风话题"
                    )
            elif input_mode == "pyaudio":
                print(f"[PyAudio-MIC] 输入模式: PyAudio 直连本地麦克风")
            elif input_mode == "ros1" and not _HAS_ROS1:
                print(
                    "[WARN] INPUT_AUDIO_MODE='ros1' 但未检测到 ROS 环境，"
                    "请在 config.py 中设置 INPUT_AUDIO_MODE='pyaudio' 或设置环境变量 INPUT_AUDIO_MODE=pyaudio"
                )

        # ---------- 播放线程 ----------
        signal.signal(signal.SIGINT, self._keyboard_signal)
        try:
            signal.signal(signal.SIGTERM, self._keyboard_signal)
        except Exception:
            pass
        try:
            signal.signal(signal.SIGHUP, self._keyboard_signal)
        except Exception:
            pass
        self.audio_queue = queue.Queue()
        if not self.is_audio_file_input:
            self.audio_device = AudioDeviceManager(
                AudioConfig(**input_cfg),
                AudioConfig(**config.output_audio_config),
            )
            # PyAudio 模式：同时打开输入流
            if input_mode == "pyaudio":
                self.input_stream = self.audio_device.open_input_stream()
                dev_info = input_cfg.get("device_index") or input_cfg.get("device_name") or "默认"
                print(f"[PyAudio-MIC] 已打开本地麦克风（设备: {dev_info}, "
                      f"采样率={input_cfg['sample_rate']}Hz, "
                      f"声道={input_cfg['channels']}）")
            self.output_stream = self.audio_device.open_output_stream()
            self.is_recording = True
            self.is_playing = True
            self.player_thread = threading.Thread(
                target=self._audio_player_thread, daemon=True
            )
            self.player_thread.start()

    # ---------- ROS 麦克风回调 ----------
    def _ros_audio_callback(self, msg: RosAudioData):
        """
        ROS 回调：收到 audio_common_msgs/AudioData 后，把原始 bytes 放进队列。
        每条消息是 wave 格式的原始 PCM（例如 0.1s @ 48k * 2ch）。
        """
        if self.ros_audio_queue is None:
            return
        try:
            pcm_bytes = bytes(msg.data)  # msg.data 是 List[int]
            try:
                self.ros_audio_queue.put_nowait(pcm_bytes)
            except queue.Full:
                # 队列满了就丢掉最早的一条，再塞新数据，避免无限堆积
                try:
                    self.ros_audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.ros_audio_queue.put_nowait(pcm_bytes)
                except Exception:
                    pass
        except Exception as e:
            print(f"[ROS-MIC] 回调处理失败: {e}")

    # ---------- MIC 指令发送 ----------
    def send_mic_command(self, command: str, cooldown_sec: float = 0.2):
        now = time.time()
        if now - self._last_mic_send_time < cooldown_sec:
            return
        try:
            msg = json.dumps(
                {"type": "mic_command", "command": command, "timestamp": now}
            )
            self.mic_udp_socket.sendto(
                msg.encode("utf-8"), (self.mic_udp_host, self.mic_udp_port)
            )
            print(f"[MIC-UDP:{self.mic_udp_port}] 发送指令：{command}")
            self._last_mic_send_time = now
        except Exception as e:
            print(f"[MIC-UDP] 发送失败: {e}")

    async def _promote_task(self):
        while self.is_running:
            await asyncio.sleep(2000000)
            if not self._is_tts_playing() and not self._promote_playing:
                await self._play_promote_message()
            else:
                while self._is_tts_playing():
                    await asyncio.sleep(1)
                await self._play_promote_message()

    async def _play_promote_message(self):
        if self._promote_playing:
            return
        self._promote_playing = True
        promote_message1 = "哦对了，欢迎了解华科智能机器人。"
        promote_message2 = "请扫旁边的二维码加入群聊。"
        print(f"开始播放推销内容: {promote_message1}")
        await self.client.chat_tts_text(False, True, False, promote_message1)
        await self.client.chat_tts_text(False, False, True, promote_message2)
        self._last_promote_ts = time.time()
        print("推销内容播放完毕")
        self._promote_playing = False

    def _remote_audio_status_callback(self, msg):
        self.remote_playing = msg.data

    def _is_ros_output(self) -> bool:
        return hasattr(self, "output_stream") and hasattr(self.output_stream, "interrupt")

    def _drain_ros_audio_queue(self) -> int:
        q = self.ros_audio_queue
        if q is None:
            return 0
        count = 0
        while True:
            try:
                q.get_nowait()
                count += 1
            except queue.Empty:
                break
        return count

    def _set_input_stream_active(self, active: bool) -> None:
        stream = getattr(self, "input_stream", None)
        if stream is None:
            return
        try:
            is_active = stream.is_active() if hasattr(stream, "is_active") else active
            if active and not is_active:
                stream.start_stream()
            elif not active and is_active:
                stream.stop_stream()
        except Exception as e:
            action = "启动" if active else "暂停"
            print(f"[Half-Duplex] {action}本地麦克风输入流失败: {e}")

    def _pause_half_duplex_mic(self, reason: str) -> None:
        if not self.block_mic_while_playing:
            return
        self._half_duplex_resume_after = (
            time.time() + self._half_duplex_resume_delay_sec
        )
        self._drain_ros_audio_queue()
        if self._half_duplex_mic_paused:
            return
        self._half_duplex_mic_paused = True
        self._set_input_stream_active(False)
        # 当前接收端约定：send_microphone -> 收话筒/关闭拾音，release_microphone -> 递话筒/恢复拾音。
        self.send_mic_command("send_microphone", cooldown_sec=0.0)
        print(f"[Half-Duplex] 暂停麦克风输入: {reason}")

    def _resume_half_duplex_mic(self, reason: str) -> None:
        if not self.block_mic_while_playing or not self._half_duplex_mic_paused:
            return
        self._drain_ros_audio_queue()
        self._set_input_stream_active(True)
        self._half_duplex_mic_paused = False
        self.send_mic_command("release_microphone", cooldown_sec=0.0)
        print(f"[Half-Duplex] 恢复麦克风输入: {reason}")

    def _hold_half_duplex_mic_if_needed(self) -> bool:
        if not self.block_mic_while_playing:
            return False

        if self._is_tts_playing():
            self._pause_half_duplex_mic("tts_playing")
            return True

        if self._half_duplex_mic_paused:
            if time.time() < self._half_duplex_resume_after:
                self._drain_ros_audio_queue()
                return True
            self._resume_half_duplex_mic("tts_finished")

        return False

    def _ros_frame_size(self) -> int:
        out_cfg = config.output_audio_config
        ms = getattr(config, "ROS_AUDIO_FRAME_MS", 20)
        tts_fmt = config.start_session_req.get("tts", {}).get("audio_config", {}).get("format", "pcm")
        if tts_fmt in ("pcm", "pcm_s16le"):
            sample_width = 2
        else:
            sample_width = pyaudio.get_sample_size(out_cfg.get("bit_size", pyaudio.paInt16))
        return (out_cfg["sample_rate"] * out_cfg["channels"] * sample_width * ms) // 1000

    def _compute_rms_16bit(self, data: bytes) -> float:
        if len(data) < 2:
            return 0.0
        count = len(data) // 2
        fmt = f"<{count}h"
        try:
            samples = struct.unpack(fmt, data)
        except Exception:
            return 0.0
        if count == 0:
            return 0.0
        return (sum(s * s for s in samples) / count) ** 0.5

    def _advance_utterance_id(self):
        self._bot_utterance_id = (int(self._bot_utterance_id) + 1) & 0x7FFFFFFF

    def _check_barge_in(self, chunk16k: bytes) -> bool:
        if self._duplex_mode != "full":
            return False
        if not getattr(config, "ENABLE_BARGE_IN", True):
            return False
        if not self._is_tts_playing():
            return False

        rms = self._compute_rms_16bit(chunk16k)
        threshold = getattr(config, "BARGE_IN_THRESHOLD", 800)
        min_frames = getattr(config, "BARGE_IN_MIN_DURATION_MS", 300) // 20

        if rms > threshold:
            self._barge_in_counter += 1
            if self._barge_in_counter >= max(min_frames, 1):
                self._interrupt_playback("local_barge_in")
                self._barge_in_counter = 0
                return True
        else:
            self._barge_in_counter = 0
        return False

    def _reset_pyaudio_output(self):
        try:
            if hasattr(self, "audio_device") and self.audio_device:
                if self.audio_device.output_stream is not None:
                    try:
                        if hasattr(self.audio_device.output_stream, "stop_stream"):
                            self.audio_device.output_stream.stop_stream()
                    except Exception:
                        pass
                    try:
                        self.audio_device.output_stream.close()
                    except Exception:
                        pass
                self.audio_device.output_stream = self.audio_device.open_output_stream()
                self.output_stream = self.audio_device.output_stream
        except Exception as e:
            print(f"[Barge-In] PyAudio output reset failed: {e}")

    def _interrupt_playback(self, reason: str = "barge_in"):
        print(f"[Barge-In] interrupt playback, reason={reason}")

        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

        should_send_remote_stop = self._is_ros_output() and (
            self._duplex_mode == "full" or reason == "session_end"
        )
        if should_send_remote_stop:
            ids_to_stop = [int(self._bot_utterance_id)]
            if self._last_sent_utterance_id is not None:
                ids_to_stop.append(int(self._last_sent_utterance_id))
                if reason == "session_end":
                    # 进程退出时把已在 ROS 管道中的后续残包窗口也一并清空。
                    ids_to_stop.extend(
                        [
                            int(self._last_sent_utterance_id) + 1,
                            int(self._last_sent_utterance_id) + 2,
                        ]
                    )
            for uid in sorted(set(ids_to_stop)):
                try:
                    self.output_stream.interrupt(uid, reason)
                except Exception as e:
                    print(f"[Barge-In] stop msg send failed(uid={uid}): {e}")

        if not self._is_ros_output():
            self._reset_pyaudio_output()

        self._advance_utterance_id()
        self._utterance_chunk_seq = 0

    def attach_stop_event(self, evt: asyncio.Event) -> None:
        self.external_stop_event = evt

    def stop(self) -> None:
        self.is_recording = False
        self.is_playing = False
        self.is_running = False

        try:
            self._interrupt_playback("session_end")
        except Exception:
            pass

        try:
            self._resume_half_duplex_mic("session_stop")
        except Exception:
            pass

        try:
            if hasattr(self, "mic_udp_socket"):
                self.mic_udp_socket.close()
        except Exception:
            pass

        # 取消 ROS 麦克风订阅
        try:
            if hasattr(self, "ros_audio_sub") and self.ros_audio_sub:
                self.ros_audio_sub.unregister()
        except Exception:
            pass
        try:
            if hasattr(self, "remote_status_sub") and self.remote_status_sub:
                self.remote_status_sub.unregister()
        except Exception:
            pass

        if self.is_audio_file_input:
            try:
                self.quit_event.set()
            except Exception:
                pass

        # 推销 TTS 任务
        try:
            if hasattr(self, "promote_task") and self.promote_task:
                self.promote_task.cancel()
        except Exception:
            pass

        # 停止前刷新未写出的机器人整段
        try:
            if hasattr(self, "_llm_text_accum") and self._llm_text_accum:
                bot_text = "".join(self._llm_text_accum).strip()
                if bot_text and bot_text != self._last_bot_text_written:
                    try:
                        self.dialog_write_queue.put_nowait(f"机器人: {bot_text}")
                        self._last_bot_text_written = bot_text
                    except Exception:
                        pass
                self._llm_text_accum.clear()
        except Exception:
            pass

        # 取消对话写入任务
        try:
            if hasattr(self, "dialog_writer_task") and self.dialog_writer_task:
                self.dialog_writer_task.cancel()
        except Exception:
            pass

    def _audio_player_thread(self):
        while self.is_playing:
            try:
                audio_data = self.audio_queue.get(timeout=1.0)
                if audio_data is not None:
                    if self._duplex_mode == "full" and self._is_ros_output():
                        self._write_framed_to_ros(audio_data)
                    else:
                        self.output_stream.write(audio_data)
                    self._last_play_ts = time.time()
            except queue.Empty:
                time.sleep(0.1)
            except Exception as e:
                print(f"音频播放错误: {e}")
                time.sleep(0.1)

    def _write_framed_to_ros(self, audio_data: bytes):
        if not audio_data:
            return
        # 不再把服务端下发块继续切得更碎，避免 full 模式 ROS 消息率过高导致丢包/跳播。
        self._last_sent_utterance_id = int(self._bot_utterance_id)
        self.output_stream.write_framed(
            self._bot_utterance_id,
            self._utterance_chunk_seq,
            True,
            audio_data,
        )
        self._utterance_chunk_seq += 1

    def _is_tts_playing(self, grace_ms: float = 300.0) -> bool:
        local_playing = (
            time.time() - self._last_play_ts
        ) * 1000.0 < grace_ms or not self.audio_queue.empty()
        return local_playing or self.remote_playing

    def _init_action_index_publisher(self):
        if not _HAS_ROS1 or rospy is None or Int32 is None:
            print("[KWS-ROS] 未检测到 ROS1，动作 index 话题不可用")
            return

        try:
            if not rospy.core.is_initialized():
                rospy.init_node(
                    "action_index_publisher", anonymous=True, disable_signals=True
                )
            self.action_index_pub = rospy.Publisher(
                self.action_index_topic,
                Int32,
                queue_size=10,
                latch=False,
            )
            print(f"[KWS-ROS] 已准备发布动作 index 话题: {self.action_index_topic}")
        except Exception as e:
            self.action_index_pub = None
            print(f"[KWS-ROS] 初始化动作 index 发布器失败: {e}")

    def _emit_voice_keyword(self, keyword: str) -> bool:
        index = ACTION_INDEX_BY_KEYWORD.get(keyword)
        if index is None:
            print(f"[KWS-ROS] 未映射关键词，跳过发布: {keyword}")
            return False

        pub = getattr(self, "action_index_pub", None)
        if pub is None:
            print(
                f"[KWS-ROS] 动作 index 发布器不可用，无法发布: "
                f"keyword={keyword}, index={index}"
            )
            return False

        # 左/右等方向关键词可配置重复发送，确保下位机可靠接收并执行多次
        repeat_count = REPEAT_ACTION_COUNT.get(keyword, 1)

        try:
            for i in range(repeat_count):
                msg = Int32(data=index) if Int32 is not None else index
                pub.publish(msg)
                if i < repeat_count - 1:
                    time.sleep(0.1)  # 间隔 100ms，避免下位机来不及处理
            print(
                f"[KWS-ROS] 发布动作 index: "
                f"keyword={keyword}, index={index}, repeat={repeat_count}, topic={self.action_index_topic}"
            )
            return True
        except Exception as e:
            print(f"[KWS-ROS] 发布动作 index 失败: {e}")
            return False

    def _maybe_emit_wave_from_asr(self, payload_msg: Dict[str, Any]):
        """
        从 ASR 的 payload 里抽取文本，使用 ASR_KWS_PATTERNS 做关键词匹配。
        """
        cand_texts = []
        for r in payload_msg.get("results", []):
            if r.get("text"):
                cand_texts.append(r["text"])
            for alt in r.get("alternatives", []):
                if alt.get("text"):
                    cand_texts.append(alt["text"])
        extra = payload_msg.get("extra", {})
        if extra.get("origin_text"):
            cand_texts.append(extra["origin_text"])
        joined = " ".join(cand_texts)
        if not joined:
            return

        # 统一用配置表做"包含匹配"，按 KWS_PRIORITY 顺序检测
        # 复合方位优先（left_front > left），允许复合+简单方位同时触发
        asr_fired: set = set()
        for keyword in KWS_PRIORITY:
            patterns = ASR_KWS_PATTERNS.get(keyword)
            if patterns is None:
                continue
            if any(p in joined for p in patterns):
                self._emit_voice_keyword(keyword)
                print(f"[ASR-KWS] 检测到关键词 '{keyword}', 已发布 ROS index")
                asr_fired.add(keyword)
        # 如果没有任何关键词命中，可在此扩展逻辑
        if not asr_fired:
            pass

    def handle_server_response(self, response: Dict[str, Any]) -> None:
        # 已移除：静默控制窗口（丢弃确认回包/文本回包）
        if response == {}:
            return
        if response["message_type"] == "SERVER_ACK" and isinstance(
            response.get("payload_msg"), bytes
        ):
            if self.is_sending_chat_tts_text:
                return
            audio_data = response["payload_msg"]
            if not self.is_audio_file_input:
                self.audio_queue.put(audio_data)
            self.audio_buffer += audio_data

        elif response["message_type"] == "SERVER_FULL_RESPONSE":
            print(f"服务器响应: {response}")
            event = response.get("event")
            payload_msg = response.get("payload_msg", {})

            # 每一轮文本回答开始（例如 event=553），重置 LLM 关键词缓冲区 & 已触发集合
            if event == 553:
                self._llm_keyword_buffer = ""
                self._llm_kws_fired.clear()
                self._advance_utterance_id()
                self._utterance_chunk_seq = 0
                # 机器人开始回答时，固定上一轮用户文本（若未写过则写入一次）
                try:
                    if (
                        not self._user_text_round_written
                    ) and self._user_text_accum.strip():
                        try:
                            self.dialog_write_queue.put_nowait(
                                f"用户: {self._user_text_accum.strip()}"
                            )
                            self._last_user_text_written = self._user_text_accum.strip()
                        except Exception:
                            pass
                    self._user_text_round_written = True
                except Exception as e:
                    print(f"[DIALOG] 固定用户文本失败: {e}")

            # 在 LLM 文本里做统一关键词检测（使用缓冲区 + 字典配置）
            if "content" in payload_msg:
                content = payload_msg["content"]

                # 1) 累积机器人整段文本（避免逐 token 写入导致频繁换行）
                if content:
                    self._llm_text_accum.append(content)

                # 1. 把当前 content 追加到缓冲区，保留最近若干字符（支持跨 token）
                self._llm_keyword_buffer += content
                if len(self._llm_keyword_buffer) > self._llm_buffer_max_len:
                    self._llm_keyword_buffer = self._llm_keyword_buffer[
                        -self._llm_buffer_max_len :
                    ]

                buf = self._llm_keyword_buffer

                # 2. 按 KWS_PRIORITY 顺序遍历 LLM_KWS_PATTERNS，检测关键短语
                #    复合方位优先（left_front > left），允许复合+简单方位同时触发
                for keyword in KWS_PRIORITY:
                    patterns = LLM_KWS_PATTERNS.get(keyword)
                    if patterns is None:
                        continue
                    # 本轮已经触发过的 keyword 不再重复触发
                    if keyword in self._llm_kws_fired:
                        continue

                    if any(p in buf for p in patterns):
                        self._emit_voice_keyword(keyword)
                        print(f"[LLM-KWS] 检测到关键词 '{keyword}', 已发布 ROS index")
                        self._llm_kws_fired.add(keyword)

                        # 如果是 end，可以选择清空缓冲，防止后续 content 再次触发
                        if keyword == "end":
                            self._llm_keyword_buffer = ""
                        # 如果希望"一次 content 只触发一个关键词"，可以在这里 break
                        # break

            if event == 451:
                self.last_user_activity_ts = time.time()
                try:
                    self._maybe_emit_wave_from_asr(payload_msg)
                except Exception as e:
                    print(f"[KWS] 解析ASR(451)失败: {e}")
                # 仅累积用户整句候选文本（451 可能多次到达，这里不写入，只保留最新）
                try:
                    cand_texts = []
                    for r in payload_msg.get("results", []):
                        if r.get("text"):
                            cand_texts.append(r["text"])
                        for alt in r.get("alternatives", []):
                            if alt.get("text"):
                                cand_texts.append(alt["text"])
                    extra = payload_msg.get("extra", {})
                    if extra.get("origin_text"):
                        cand_texts.append(extra["origin_text"])
                    user_text_joined = " ".join(cand_texts).strip()
                    if user_text_joined:
                        self._user_text_accum = user_text_joined
                except Exception as e:
                    print(f"[DIALOG] 累积用户文本失败: {e}")

            if event == 450:
                print(f"清空缓存音频: {response['session_id']}")
                should_interrupt = self._duplex_mode != "full" or bool(
                    getattr(config, "FULL_DUPLEX_INTERRUPT_ON_EVENT450", False)
                )
                if should_interrupt:
                    self._interrupt_playback("user_speech")
                self.is_user_querying = True
                # 用户新一轮开始：清理累积并标记未写
                self._user_text_accum = ""
                self._user_text_round_written = False
                self.last_user_activity_ts = time.time()

            if (
                event == 350
                and self.is_sending_chat_tts_text
                and payload_msg.get("tts_type") == "chat_tts_text"
            ):
                while not self.audio_queue.empty():
                    try:
                        self.audio_queue.get_nowait()
                    except queue.Empty:
                        continue
                self.is_sending_chat_tts_text = False

            if event == 459:
                self.is_user_querying = False
                # 若本轮用户文本还未写入，兜底写一次，避免漏日志
                try:
                    if (
                        not self._user_text_round_written
                    ) and self._user_text_accum.strip():
                        try:
                            self.dialog_write_queue.put_nowait(
                                f"用户: {self._user_text_accum.strip()}"
                            )
                            self._last_user_text_written = self._user_text_accum.strip()
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[DIALOG] 兜底写入用户文本失败: {e}")
                # 结束一轮后清理累积
                self._user_text_accum = ""
                self._user_text_round_written = False
                # 机器人一轮回答已彻底结束，写入整段文本
                try:
                    if self._llm_text_accum:
                        bot_text = "".join(self._llm_text_accum).strip()
                        if bot_text and bot_text != self._last_bot_text_written:
                            try:
                                self.dialog_write_queue.put_nowait(
                                    f"机器人: {bot_text}"
                                )
                                self._last_bot_text_written = bot_text
                            except Exception:
                                pass
                    # 重置累积区
                    self._llm_text_accum.clear()
                except Exception as e:
                    print(f"[DIALOG] 写入机器人文本失败: {e}")
                # 一轮回答彻底结束，也可以顺便清空关键词状态（可选，如果你感觉"左"偶尔不触发，可以用这句）
                # self._llm_keyword_buffer = ""
                # self._llm_kws_fired.clear()
                if random.randint(0, 10000) == 0:
                    self.is_sending_chat_tts_text = True
                    asyncio.create_task(self.trigger_chat_tts_text())

        elif response["message_type"] == "SERVER_ERROR":
            print(f"服务器错误: {response['payload_msg']}")
            raise Exception("服务器错误")

    async def _send_silence_if_due(self):
        now = time.time()
        if (now - self._last_silence_ts) >= self._silence_interval_sec:
            await self.process_silence_audio()
            self._last_silence_ts = now

    async def trigger_chat_tts_text(self):
        print("hit ChatTTSText event, start sending...")
        await self.client.chat_tts_text(
            self.is_user_querying,
            True,
            False,
            "这是第一轮TTS的开始和中间包事件，这两个合而为一了。",
        )
        await self.client.chat_tts_text(
            self.is_user_querying, False, True, "这是第一轮TTS的结束事件。"
        )
        await asyncio.sleep(10)
        await self.client.chat_tts_text(
            self.is_user_querying,
            True,
            False,
            "这是第二轮TTS的开始和中间包事件，这两个合而为一了。",
        )
        await self.client.chat_tts_text(
            self.is_user_querying, False, True, "这是第二轮TTS的结束事件。"
        )

    def _keyboard_signal(self, sig, frame):
        print("receive keyboard Ctrl+C")
        self.stop()

    async def receive_loop(self):
        try:
            while True:
                response = await self.client.receive_server_response()
                self.handle_server_response(response)
                if "event" in response and (
                    response["event"] == 152 or response["event"] == 153
                ):
                    print(f"receive session finished event: {response['event']}")
                    self.is_session_finished = True
                    break
                if (
                    self.is_audio_file_input
                    and "event" in response
                    and response["event"] == 359
                ):
                    print("receive tts ended event")
                    self.is_session_finished = True
                    break
                if (
                    not self.is_audio_file_input
                    and "event" in response
                    and response["event"] == 359
                    and not self.say_hello_over_event.is_set()
                ):
                    print("receive tts sayhello ended event")
                    self.say_hello_over_event.set()
        except asyncio.CancelledError:
            print("接收任务已取消")
        except Exception as e:
            print(f"接收消息错误: {e}")

    async def process_audio_file(self) -> None:
        await self.process_audio_file_input(self.audio_file_path)
        while not self.quit_event.is_set():
            try:
                await asyncio.sleep(0.01)
                if self.quit_event.is_set():
                    break
                await self.process_silence_audio()
            except Exception as e:
                print(f"发送音频失败: {e}")
                raise

    async def process_audio_file_input(self, audio_file_path: str) -> None:
        with wave.open(audio_file_path, "rb") as wf:
            chunk_size = config.input_audio_config["chunk"]
            print(f"开始处理音频文件: {audio_file_path}")
            while True:
                audio_data = wf.readframes(chunk_size)
                if not audio_data:
                    break
                await self.client.task_request(audio_data)
            print("音频文件处理完成，等待服务器响应...")

    async def process_silence_audio(self) -> None:
        silence_data = b"\x00" * (TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES)
        await self.client.task_request(silence_data)

    def _resample_to_16k(self, pcm16_le_mono: bytes, in_rate: int) -> bytes:
        if in_rate == TARGET_SAMPLE_RATE:
            return pcm16_le_mono
        converted, self._ratecv_state = audioop.ratecv(
            pcm16_le_mono,
            TARGET_SAMPLE_WIDTH,
            TARGET_CHANNELS,
            in_rate,
            TARGET_SAMPLE_RATE,
            self._ratecv_state,
        )
        return converted

    async def process_microphone_input(self) -> None:
        """
        麦克风输入入口，根据 INPUT_AUDIO_MODE 分发到对应循环：
          - "ros1": 从 ROS 话题 /audio/audio 读取
          - "pyaudio": 从本地 PyAudio 输入流读取
        """
        if self.block_mic_while_playing:
            self._pause_half_duplex_mic("say_hello")
        await self.client.say_hello()
        await self.say_hello_over_event.wait()
        if self.block_mic_while_playing and self._half_duplex_mic_paused:
            self._half_duplex_resume_after = (
                time.time() + self._half_duplex_resume_delay_sec
            )
        while self._hold_half_duplex_mic_if_needed():
            await self._send_silence_if_due()
            await asyncio.sleep(0.02)
        await self.client.chat_text_query(self.start_prompt)

        input_cfg = config.get_input_audio_config()
        in_rate = input_cfg["sample_rate"]
        in_channels = input_cfg["channels"]
        in_width = 2

        input_mode = getattr(config, "INPUT_AUDIO_MODE", "ros1")
        if input_mode == "pyaudio":
            print(
                f"[PyAudio-MIC] 使用本地麦克风，采样率={in_rate}Hz, channels={in_channels}，开始讲话..."
            )
            await self._mic_loop_pyaudio(in_rate, in_channels, in_width)
        else:
            print(
                f"[ROS-MIC] 使用 ROS /audio/audio，采样率={in_rate}Hz, channels={in_channels}，开始讲话..."
            )
            await self._mic_loop_ros(in_rate, in_channels, in_width)

    async def _mic_loop_ros(self, in_rate: int, in_channels: int, in_width: int):
        """ROS 模式：从 ros_audio_queue 读取麦克风数据。"""
        def to_mono(pcm_bytes: bytes) -> bytes:
            if in_channels == 1:
                return pcm_bytes
            try:
                return audioop.tomono(pcm_bytes, in_width, 0.5, 0.5)
            except Exception as e:
                print(f"[ROS-MIC] tomono 失败，直接使用原始音频: {e}")
                return pcm_bytes

        while self.is_recording:
            try:
                if self.external_stop_event and self.external_stop_event.is_set():
                    self.stop()
                    break

                if self._hold_half_duplex_mic_if_needed():
                    await self._send_silence_if_due()
                    await asyncio.sleep(0.02)
                    continue

                if self.ros_audio_queue is None:
                    await asyncio.sleep(0.01)
                    continue

                try:
                    audio_data = self.ros_audio_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.01)
                    continue

                mono = to_mono(audio_data)
                pcm16_16k = self._resample_to_16k(mono, in_rate)

                frame_bytes = TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES
                total_len = len(pcm16_16k)
                offset = 0

                while total_len - offset >= frame_bytes:
                    chunk16k = pcm16_16k[offset : offset + frame_bytes]
                    offset += frame_bytes

                    if self._duplex_mode == "full":
                        self._check_barge_in(chunk16k)
                    await self.client.task_request(chunk16k)

                await asyncio.sleep(0.005)
            except Exception as e:
                print(f"从 ROS 队列读取麦克风数据出错: {e}")
                await asyncio.sleep(0.1)

    async def _mic_loop_pyaudio(self, in_rate: int, in_channels: int, in_width: int):
        """PyAudio 模式：从本地麦克风输入流读取数据。"""
        stream = self.input_stream
        if stream is None:
            print("[PyAudio-MIC] 错误：输入流未初始化")
            return

        chunk_size = config.get_input_audio_config()["chunk"]

        def to_mono(pcm_bytes: bytes) -> bytes:
            if in_channels == 1:
                return pcm_bytes
            try:
                return audioop.tomono(pcm_bytes, in_width, 0.5, 0.5)
            except Exception as e:
                print(f"[PyAudio-MIC] tomono 失败，直接使用原始音频: {e}")
                return pcm_bytes

        while self.is_recording:
            try:
                if self.external_stop_event and self.external_stop_event.is_set():
                    self.stop()
                    break

                if self._hold_half_duplex_mic_if_needed():
                    await self._send_silence_if_due()
                    await asyncio.sleep(0.02)
                    continue

                # 非阻塞读取 PyAudio 流（通过 executor 避免阻塞事件循环）
                audio_data = await _to_thread(
                    stream.read, chunk_size, False  # exception_on_overflow=False
                )

                mono = to_mono(audio_data)
                pcm16_16k = self._resample_to_16k(mono, in_rate)

                frame_bytes = TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES
                total_len = len(pcm16_16k)
                offset = 0

                while total_len - offset >= frame_bytes:
                    chunk16k = pcm16_16k[offset : offset + frame_bytes]
                    offset += frame_bytes

                    if self._duplex_mode == "full":
                        self._check_barge_in(chunk16k)
                    await self.client.task_request(chunk16k)

                await asyncio.sleep(0.005)
            except Exception as e:
                print(f"[PyAudio-MIC] 读取麦克风数据出错: {e}")
                await asyncio.sleep(0.1)

    async def start(self) -> None:
        try:
            await self.client.connect()
            # 启动异步写入任务（追加到历史对话，不再清空文件）
            self.dialog_writer_task = asyncio.create_task(self._dialog_writer())
            if self.is_audio_file_input:
                asyncio.create_task(self.process_audio_file())
                await self.receive_loop()
                self.quit_event.set()
                await asyncio.sleep(0.1)
            else:
                asyncio.create_task(self.process_microphone_input())
                asyncio.create_task(self.receive_loop())
                while self.is_running:
                    if self.external_stop_event and self.external_stop_event.is_set():
                        self.stop()
                        break
                    await asyncio.sleep(0.1)

            await self.client.finish_session()
            while not self.is_session_finished:
                await asyncio.sleep(0.1)
            await self.client.finish_connection()
            await asyncio.sleep(0.1)
            await self.client.close()
            print(f"dialog request logid: {self.client.logid}")
            save_output_to_file(self.audio_buffer, "output.pcm")
        except Exception as e:
            print(f"会话错误: {e}")
        finally:
            try:
                self.stop()
            except Exception:
                pass
            if not self.is_audio_file_input:
                self.audio_device.cleanup()

    async def _dialog_writer(self) -> None:
        """
        将队列中的文本批量、逐行写入 dialog.txt。
        通过小批量与异步休眠，避免频繁文件 IO 导致延迟。
        """
        try:
            while self.is_running:
                try:
                    # 等待最多 100ms 收集一批文本，降低 IO 次数
                    batch: list[str] = []
                    try:
                        # 立即获取可用项
                        while True:
                            item = self.dialog_write_queue.get_nowait()
                            batch.append(item)
                            if len(batch) >= 50:
                                break
                    except Exception:
                        pass

                    if not batch:
                        # 若无数据，短暂等待后重试
                        await asyncio.sleep(0.1)
                        continue

                    # 追加写入，逐行
                    try:
                        with open(self.dialog_file_path, "a", encoding="utf-8") as f:
                            for line in batch:
                                f.write(line.replace("\r\n", "\n").replace("\r", "\n"))
                                f.write("\n")
                    except Exception as e:
                        print(f"[DIALOG-WRITER] 写入失败: {e}")

                    # 轻微休眠，避免紧密循环
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print(f"[DIALOG-WRITER] 任务异常: {e}")
                    await asyncio.sleep(0.1)
        finally:
            # 退出前尝试写入残留数据
            try:
                remaining: list[str] = []
                try:
                    while True:
                        remaining.append(self.dialog_write_queue.get_nowait())
                except Exception:
                    pass
                if remaining:
                    with open(self.dialog_file_path, "a", encoding="utf-8") as f:
                        for line in remaining:
                            f.write(line.replace("\r\n", "\n").replace("\r", "\n"))
                            f.write("\n")
            except Exception:
                pass
