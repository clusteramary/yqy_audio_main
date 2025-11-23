import asyncio
import audioop  # 标准库：重采样
import json
import os
import queue
import random
import re
import signal
import socket
import sys
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import pyaudio

import config
from realtime_dialog_client import RealtimeDialogClient

# === 改：使用“共享队列版” SAUC 识别，而不是再开一条麦克风 ===
from sauc_python.sauc_websocket_mic2 import recognize_once_from_queue

try:
    _to_thread = asyncio.to_thread  # Python 3.9+
except AttributeError:
    import functools

    async def _to_thread(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(func, *args, **kwargs)
        )


try:
    import rospy
    from std_msgs.msg import Bool, ByteMultiArray

    try:
        from audio_common_msgs.msg import AudioData as RosAudioData

        _HAS_AUDIO_DATA_MSG = True
    except Exception:
        _HAS_AUDIO_DATA_MSG = False
    _HAS_ROS1 = True
except Exception:
    _HAS_ROS1 = False
    _HAS_AUDIO_DATA_MSG = False

TARGET_SAMPLE_RATE = 16000
TARGET_SAMPLE_WIDTH = 2
TARGET_CHANNELS = 1
TARGET_CHUNK_SAMPLES = 320  # 16k * 20ms = 320 样本 → 每帧约 20ms

# ASR 关键词配置：标签 -> 若干“包含匹配”的短语（用于语音识别结果）
ASR_KWS_PATTERNS: Dict[str, list] = {
    "wave": ["挥手", "挥一挥", "挥一下", "握一下手", "wave"],
    "nod": ["点头", "点一下", "nod"],
    "shake": ["击掌", "击一下"],
    "woshou": ["握手", "握一下", "握个手", "shake"],
    "end": ["再见", "拜拜", "bye"],
}

# LLM 文本关键词配置：标签 -> 若干“包含匹配”的短语（用于大模型文本 content）
LLM_KWS_PATTERNS: Dict[str, list] = {
    "left": ["向左", "往左"],
    "right": ["向右", "往右", "测试成功啦"],
    # 结束访谈/结束控制，由 LLM 说出
    "end": ["感谢你", "感谢您", "感谢"],
    # 后面可以继续加，比如:
    # "stop": ["停一下", "停止", "先到这里"],
}


@dataclass
class AudioConfig:
    format: str
    bit_size: int
    channels: int
    sample_rate: int
    chunk: int
    device_index: Optional[int] = None
    mode: str = "pyaudio"
    ros1_topic: str = "/robot/speaker/audio"
    ros1_node_name: str = "speaker_publisher"
    ros1_queue_size: int = 10
    ros1_latch: bool = False


class Ros1SpeakerStream:
    def __init__(
        self,
        topic="/robot/speaker/audio",
        node_name="speaker_publisher",
        queue_size=10,
        latched=False,
    ):
        if not _HAS_ROS1:
            raise RuntimeError("未检测到 ROS1 (rospy)。请在 ROS1 环境运行。")
        if not rospy.core.is_initialized():
            rospy.init_node(node_name, anonymous=True, disable_signals=True)
        self.topic = topic
        self._use_audio_msg = _HAS_AUDIO_DATA_MSG
        if self._use_audio_msg:
            self._pub = rospy.Publisher(
                topic, RosAudioData, queue_size=queue_size, latch=latched
            )
        else:
            self._pub = rospy.Publisher(
                topic, ByteMultiArray, queue_size=queue_size, latch=latched
            )
        self._closed = False

    def write(self, audio_bytes: bytes):
        if self._closed:
            return
        if self._use_audio_msg:
            msg = RosAudioData()
            msg.data = list(audio_bytes)
        else:
            msg = ByteMultiArray()
            msg.data = list(audio_bytes)
        self._pub.publish(msg)

    def stop_stream(self):
        pass

    def close(self):
        self._closed = True


class AudioDeviceManager:
    def __init__(self, input_config: AudioConfig, output_config: AudioConfig):
        self.input_config = input_config
        self.output_config = output_config
        self.pyaudio = pyaudio.PyAudio()
        self.input_stream: Optional[pyaudio.Stream] = None
        self.output_stream: Optional[Any] = None

    def open_input_stream(self) -> pyaudio.Stream:
        open_kwargs = dict(
            format=self.input_config.bit_size,
            channels=self.input_config.channels,
            rate=self.input_config.sample_rate,
            input=True,
            frames_per_buffer=self.input_config.chunk,
        )
        if self.input_config.device_index is not None:
            open_kwargs["input_device_index"] = self.input_config.device_index
        self.input_stream = self.pyaudio.open(**open_kwargs)
        return self.input_stream

    def open_output_stream(self) -> Any:
        mode = (self.output_config.mode or "pyaudio").lower()
        if mode == "pyaudio":
            open_kwargs = dict(
                format=self.output_config.bit_size,
                channels=self.output_config.channels,
                rate=self.output_config.sample_rate,
                output=True,
                frames_per_buffer=self.output_config.chunk,
            )
            if self.output_config.device_index is not None:
                open_kwargs["output_device_index"] = self.output_config.device_index
            self.output_stream = self.pyaudio.open(**open_kwargs)
            return self.output_stream
        elif mode == "ros1":
            self.output_stream = Ros1SpeakerStream(
                topic=self.output_config.ros1_topic,
                node_name=self.output_config.ros1_node_name,
                queue_size=self.output_config.ros1_queue_size,
                latched=self.output_config.ros1_latch,
            )
            return self.output_stream
        else:
            raise ValueError(f"未知输出模式：{mode!r}")

    def cleanup(self) -> None:
        if isinstance(self.input_stream, pyaudio.Stream):
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception:
                pass
        self.input_stream = None
        if self.output_stream is not None:
            try:
                if hasattr(self.output_stream, "stop_stream"):
                    self.output_stream.stop_stream()
                if hasattr(self.output_stream, "close"):
                    self.output_stream.close()
            except Exception:
                pass
        self.output_stream = None
        try:
            self.pyaudio.terminate()
        except Exception:
            pass


class DialogSession:
    is_audio_file_input: bool

    def __init__(
        self,
        ws_config: Dict[str, Any],
        start_prompt: str,
        output_audio_format: str = "pcm",
        audio_file_path: str = "",
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
        self.block_mic_while_playing = True

        self._last_silence_ts = 0.0
        self._silence_interval_sec = 0.20

        self.external_stop_event: Optional[asyncio.Event] = None

        self.remote_playing = False
        self.remote_status_topic = "/audio_playing_status"
        self.remote_status_sub = None

        # === 语音关键词 / MIC 指令通道 ===
        self.voice_udp_host = "127.0.0.1"
        self.voice_udp_port = 5557
        self.voice_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.mic_udp_host = "127.0.0.1"
        self.mic_udp_port = 5558
        self.mic_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # 关键词“挥手” 及去抖（KWS 通道，当前未直接使用 _wave_re，但保留字段不影响）
        self._wave_re = re.compile(r"(挥手|揮手|招手|挥个手|挥下手)")
        self._kws_wave_cooldown = 1.5
        self._kws_wave_last_ts = 0.0

        # 话筒指令冷却
        self._last_mic_send_time = 0.0

        # === LLM 输出关键短语检测缓冲（用于跨 token 检测“感谢你”“左”“右”等） ===
        self._llm_keyword_buffer: str = ""
        self._llm_buffer_max_len: int = 50  # 只保留最近若干字符即可
        # 当前这一轮 LLM 回复中已经触发过的关键词，避免同一轮触发多次
        self._llm_kws_fired: Set[str] = set()

        # === 控制 ctrl.txt 的文本触发 + SAUC 识别 ===
        # 是否处于“因为 ctrl 流程而暂停向大模型上传真实麦克风数据”的状态
        self._pause_mic_for_ctrl = False

        # 共享 16k PCM 帧队列：主采集线程写，ctrl + SAUC 从这里读
        # 帧格式：s16le 单声道，采样率 16000，每帧 TARGET_CHUNK_SAMPLES 样本 ≈ 20ms
        self.ctrl_frame_queue: asyncio.Queue = asyncio.Queue()

        # sauc_python 目录 & 文件路径（相对于当前文件）
        self.sauc_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "sauc_python"
        )
        self.ctrl_file_path = os.path.join(self.sauc_dir, "ctrl.txt")
        self.output_file_path = os.path.join(self.sauc_dir, "output.txt")

        # ctrl 相关状态：
        #   - _last_ctrl_text：用于检测 ctrl.txt 是否有变化
        #   - _ctrl_pending_text：最新的控制信号内容，等待“下一轮用户说话”
        #   - _ctrl_worker_task：负责“等模型空闲 → 捕获下一轮说话 → 发送 ctrl+语音文本” 的协程任务
        self._last_ctrl_text: Optional[str] = None
        self._ctrl_pending_text: Optional[str] = None
        self._ctrl_worker_task: Optional[asyncio.Task] = None

        self.ctrl_monitor_task: Optional[asyncio.Task] = None
        if not self.is_audio_file_input:
            # 只在麦克风模式下监控 ctrl
            self.ctrl_monitor_task = asyncio.create_task(self._monitor_ctrl_file())

        if not self.is_audio_file_input and _HAS_ROS1:
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

        signal.signal(signal.SIGINT, self._keyboard_signal)
        self.audio_queue = queue.Queue()
        if not self.is_audio_file_input:
            self.audio_device = AudioDeviceManager(
                AudioConfig(**config.input_audio_config),
                AudioConfig(**config.output_audio_config),
            )
            self.output_stream = self.audio_device.open_output_stream()
            self.is_recording = True
            self.is_playing = True
            self.player_thread = threading.Thread(
                target=self._audio_player_thread, daemon=True
            )
            self.player_thread.start()

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

    # ---------- 监控 ctrl.txt ----------
    async def _monitor_ctrl_file(self):
        """
        监控 sauc_python/ctrl.txt 内容变化：
        一旦 ctrl 内容变化，不立刻打断当前对话，而是：
          1）只记录“待发送的 ctrl 文本”到 _ctrl_pending_text；
          2）启动一个后台协程 _ctrl_utterance_worker：
             - 等这一轮 TTS 播放完、大模型空闲；
             - 使用 SAUC 队列版 ASR 捕获「用户下一句说话」；
             - 将 ctrl 文本 + 该句文本合并，作为“下一轮自然对话”的输入发给大模型。
        """
        if not os.path.isdir(self.sauc_dir):
            print(f"[CTRL-MONITOR] 未找到目录: {self.sauc_dir}，跳过 ctrl 监控。")
            return

        print(f"[CTRL-MONITOR] 启动，监控 ctrl: {self.ctrl_file_path}")

        while self.is_running:
            try:
                try:
                    with open(self.ctrl_file_path, "r", encoding="utf-8") as f:
                        current_ctrl = f.read().strip()
                except FileNotFoundError:
                    # ctrl.txt 还没建好，稍后再试
                    await asyncio.sleep(0.5)
                    continue

                if self._last_ctrl_text is None:
                    # 第一次读取，只当基线，不触发逻辑
                    self._last_ctrl_text = current_ctrl
                elif current_ctrl != self._last_ctrl_text:
                    self._last_ctrl_text = current_ctrl
                    await self._handle_ctrl_file_change(current_ctrl)

                await asyncio.sleep(0.3)
            except asyncio.CancelledError:
                print("[CTRL-MONITOR] 监控任务被取消")
                break
            except Exception as e:
                print(f"[CTRL-MONITOR] 监控异常: {e}")
                await asyncio.sleep(0.5)

    async def _handle_ctrl_file_change(self, ctrl_text: str) -> None:
        """
        ctrl.txt 内容改变时调用：
        不再立刻调用 SAUC / chat_text_query，而是：
          - 只更新 _ctrl_pending_text；
          - 如果没有正在运行的 ctrl worker，则启动一个。
        """
        try:
            print(f"[CTRL-MONITOR] 检测到 ctrl.txt 内容变化，内容: {ctrl_text!r}")
            # 更新“待绑定到下一轮说话”的控制文本
            self._ctrl_pending_text = ctrl_text

            # 若已有 worker 在跑，则只更新 pending 内容即可
            if self._ctrl_worker_task is not None and not self._ctrl_worker_task.done():
                print(
                    "[CTRL-MONITOR] 已有 ctrl worker 在运行，更新 pending 文本后等待其完成。"
                )
                return

            # 启动新的后台 worker
            self._ctrl_worker_task = asyncio.create_task(self._ctrl_utterance_worker())
            print("[CTRL-MONITOR] 启动 ctrl worker，等待下一轮自然说话。")

        except Exception as e:
            print(f"[CTRL-MONITOR] 处理 ctrl 变化失败: {e}")

    async def _ctrl_utterance_worker(self):
        """
        背景任务：
        1）等待当前一轮 TTS 播放结束，大模型空闲（不在回复中）；
        2）启用“ctrl 捕获模式”：主采集线程仍然读麦克风，但：
           - 实际音频帧推到 ctrl_frame_queue；
           - 向大模型端只发静音帧保持会话；
        3）使用 SAUC 队列版一次性 ASR，从 ctrl_frame_queue 捕获“用户下一句说话”；
        4）若成功识别到文本 asr_text，则将 (ctrl_pending_text + asr_text) 合并成一条文本，
           用 chat_text_query 发给大模型，作为“下一轮自然对话”的输入；
        5）如果一直没识别出有效文本，本次控制信号不会被丢弃，
           而是继续挂起等待下一轮语音，直到有文本才真正发送。
        """
        try:
            # 启动时的 ctrl 文本快照（期间 ctrl.txt 再变更，会走下一轮 worker）
            initial_ctrl_text = self._ctrl_pending_text
            if not initial_ctrl_text:
                print("[CTRL-WORKER] 启动时 ctrl_pending_text 为空，直接退出。")
                return

            print(
                f"[CTRL-WORKER] 启动，等待模型空闲后捕获下一句说话，ctrl_text={initial_ctrl_text!r}"
            )

            attempt = 0
            # 只要会话还在、且还有挂起的 ctrl，就一直守着这次控制信号
            while self.is_running and self._ctrl_pending_text:
                attempt += 1
                print(f"[CTRL-WORKER] 开始第 {attempt} 次 ctrl 语音捕获尝试...")

                # 1）等待：模型不在回复中 & 不在播放 TTS
                while self.is_running:
                    if (not self.is_user_querying) and (not self._is_tts_playing()):
                        break
                    await asyncio.sleep(0.1)

                if not self.is_running:
                    print("[CTRL-WORKER] 会话已结束，提前退出。")
                    return

                # 清空上一轮可能残留的 ctrl 帧
                try:
                    while not self.ctrl_frame_queue.empty():
                        self.ctrl_frame_queue.get_nowait()
                except Exception:
                    pass

                # 2）开启 ctrl 捕获模式：主采集线程会把 16k 帧扔进 ctrl_frame_queue，并对大模型只发静音
                self._pause_mic_for_ctrl = True

                print(
                    "[CTRL-WORKER] 模型已空闲，启用 ctrl 捕获模式，从共享队列采集下一句语音..."
                )

                # 3）调用 SAUC 队列版一次性 ASR（内部带 VAD：检测到一段语音 -> 静音 -> 结束）
                try:
                    asr_text = await recognize_once_from_queue(
                        self.ctrl_frame_queue,
                        output_file=self.output_file_path,
                        frame_ms=int(1000 * TARGET_CHUNK_SAMPLES / TARGET_SAMPLE_RATE),
                        vad_silence_ms=500,
                        vad_threshold=500,
                        max_record_ms=15000,
                    )
                except Exception as e:
                    print(f"[CTRL-WORKER] 调用 SAUC 队列版失败: {e}")
                    asr_text = ""
                finally:
                    # 关闭 ctrl 捕获模式：主采集线程恢复正常，把音频直接发给大模型
                    self._pause_mic_for_ctrl = False
                    # 清空队列残帧，避免下次启动时受影响
                    try:
                        while not self.ctrl_frame_queue.empty():
                            self.ctrl_frame_queue.get_nowait()
                    except Exception:
                        pass

                print(f"[CTRL-WORKER] 第 {attempt} 次 SAUC 队列识别结果：{asr_text!r}")

                # 4）根据“控制信号 + 语音文本”是否齐全，决定是否发给大模型
                if not asr_text:
                    # 不再认为“控制信号本次作废”，只打印日志，保留 _ctrl_pending_text
                    print(
                        "[CTRL-WORKER] 本轮未检测到有效语音，控制信号继续挂起，等待下一轮语音。"
                    )
                    # 小睡一下避免紧密循环
                    await asyncio.sleep(0.2)
                    continue

                # 仍以最新 pending ctrl 文本为准（中途可能被覆盖）
                ctrl_text_final = self._ctrl_pending_text or initial_ctrl_text
                pieces = []
                if ctrl_text_final:
                    pieces.append(ctrl_text_final)
                pieces.append(asr_text)
                combined_text = "\n".join(pieces)

                print(
                    f"[CTRL-WORKER] 发送“控制信号 + 本轮语音”到大模型：{combined_text!r}"
                )
                try:
                    await self.client.chat_text_query(combined_text)
                except Exception as e:
                    print(f"[CTRL-WORKER] chat_text_query 发送失败: {e}")

                # 本轮控制信号已成功消耗
                self._ctrl_pending_text = None
                print("[CTRL-WORKER] 本轮 ctrl 已消耗，worker 结束。")
                return

            print("[CTRL-WORKER] 退出：会话已结束或 ctrl_pending_text 已被清空。")

        except asyncio.CancelledError:
            print("[CTRL-WORKER] 被取消，结束。")
            raise
        except Exception as e:
            print(f"[CTRL-WORKER] 运行异常: {e}")
        finally:
            # 收尾兜底：无论如何都确保关闭 ctrl 捕获模式，避免主采集线程长期只发静音
            self._pause_mic_for_ctrl = False
            try:
                while not self.ctrl_frame_queue.empty():
                    self.ctrl_frame_queue.get_nowait()
            except Exception:
                pass

    def _remote_audio_status_callback(self, msg):
        self.remote_playing = msg.data

    def attach_stop_event(self, evt: asyncio.Event) -> None:
        self.external_stop_event = evt

    def stop(self) -> None:
        self.is_recording = False
        self.is_playing = False
        self.is_running = False
        try:
            if hasattr(self, "voice_udp_socket"):
                self.voice_udp_socket.close()
        except Exception:
            pass
        try:
            if hasattr(self, "mic_udp_socket"):
                self.mic_udp_socket.close()
        except Exception:
            pass

        if self.is_audio_file_input:
            try:
                self.quit_event.set()
            except Exception:
                pass

        # 推广播报任务
        try:
            if hasattr(self, "promote_task") and self.promote_task:
                self.promote_task.cancel()
        except Exception:
            pass

        # 取消 ctrl 监控任务
        try:
            if hasattr(self, "ctrl_monitor_task") and self.ctrl_monitor_task:
                self.ctrl_monitor_task.cancel()
        except Exception:
            pass

        # 取消 ctrl worker 任务
        try:
            if hasattr(self, "_ctrl_worker_task") and self._ctrl_worker_task:
                self._ctrl_worker_task.cancel()
        except Exception:
            pass

    def _audio_player_thread(self):
        while self.is_playing:
            try:
                audio_data = self.audio_queue.get(timeout=1.0)
                if audio_data is not None:
                    self.output_stream.write(audio_data)
                    self._last_play_ts = time.time()
                    # 播放完成后 → 递话筒（独立 MIC 通道）
                    self.send_mic_command("send_microphone")
            except queue.Empty:
                time.sleep(0.1)
            except Exception as e:
                print(f"音频播放错误: {e}")
                time.sleep(0.1)

    def _is_tts_playing(self, grace_ms: float = 150.0) -> bool:
        local_playing = (
            time.time() - self._last_play_ts
        ) * 1000.0 < grace_ms or not self.audio_queue.empty()
        return local_playing or self.remote_playing

    def _emit_voice_keyword(self, keyword: str):
        try:
            now = time.time()
            msg = json.dumps(
                {"type": "voice_keyword", "keyword": keyword, "timestamp": now}
            )
            self.voice_udp_socket.sendto(
                msg.encode("utf-8"), (self.voice_udp_host, self.voice_udp_port)
            )
            print(f"[KWS] 发送语音关键词：{keyword}")
        except Exception as e:
            print(f"[KWS] 发送UDP失败: {e}")

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

        # 统一用配置表做“包含匹配”，方便后续扩展
        for keyword, patterns in ASR_KWS_PATTERNS.items():
            if any(p in joined for p in patterns):
                self._emit_voice_keyword(keyword)
                print(f"[ASR-KWS] 检测到关键词 '{keyword}', 已发送 UDP 消息")
                break

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

            # 在 LLM 文本里做统一关键词检测（使用缓冲区 + 字典配置）
            if "content" in payload_msg:
                content = payload_msg["content"]

                # 1. 把当前 content 追加到缓冲区，保留最近若干字符（支持跨 token）
                self._llm_keyword_buffer += content
                if len(self._llm_keyword_buffer) > self._llm_buffer_max_len:
                    self._llm_keyword_buffer = self._llm_keyword_buffer[
                        -self._llm_buffer_max_len :
                    ]

                buf = self._llm_keyword_buffer

                # 2. 遍历 LLM_KWS_PATTERNS，检测关键短语
                for keyword, patterns in LLM_KWS_PATTERNS.items():
                    # 本轮已经触发过的 keyword 不再重复触发
                    if keyword in self._llm_kws_fired:
                        continue

                    if any(p in buf for p in patterns):
                        self._emit_voice_keyword(keyword)
                        print(f"[LLM-KWS] 检测到关键词 '{keyword}', 已发送 UDP 消息")
                        self._llm_kws_fired.add(keyword)

                        # 如果是 end，可以选择清空缓冲，防止后续 content 再次触发
                        if keyword == "end":
                            self._llm_keyword_buffer = ""
                        # 如果希望“一次 content 只触发一个关键词”，可以在这里 break
                        # break

            if event == 451:
                try:
                    self._maybe_emit_wave_from_asr(payload_msg)
                except Exception as e:
                    print(f"[KWS] 解析ASR(451)失败: {e}")

            if event == 450:
                print(f"清空缓存音频: {response['session_id']}")
                while not self.audio_queue.empty():
                    try:
                        self.audio_queue.get_nowait()
                    except queue.Empty:
                        continue
                self.is_user_querying = True

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
        print(f"receive keyboard Ctrl+C")
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
        统一采集线程：
          - 只这一处打开 PyAudio 输入流；
          - 把数据转换成 16k/s16le 单声道 20ms 帧；
          - 正常情况下：把这些帧发给大模型；
          - 若 _pause_mic_for_ctrl=True：这些帧推给 ctrl_frame_queue，
            同时对大模型只发静音帧（保持会话但不让这句话走“正常语音通路”）。
        """
        await self.client.say_hello()
        await self.say_hello_over_event.wait()
        await self.client.chat_text_query(self.start_prompt)

        stream = self.audio_device.open_input_stream()
        print(
            f"已打开麦克风（device_index={config.input_audio_config.get('device_index')}），"
            f"采样率={config.input_audio_config['sample_rate']}，chunk={config.input_audio_config['chunk']}，开始讲话..."
        )

        in_rate = config.input_audio_config["sample_rate"]
        in_channels = config.input_audio_config["channels"]
        in_width = 2

        def to_mono(pcm_bytes: bytes) -> bytes:
            if in_channels == 1:
                return pcm_bytes
            return audioop.tomono(pcm_bytes, in_width, 0.5, 0.5)

        while self.is_recording:
            try:
                if self.external_stop_event and self.external_stop_event.is_set():
                    self.stop()
                    break

                # 统一从 PyAudio 读一块数据
                audio_data = stream.read(
                    config.input_audio_config["chunk"], exception_on_overflow=False
                )
                mono = to_mono(audio_data)
                pcm16_16k = self._resample_to_16k(mono, in_rate)

                frame_bytes = TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES
                total_len = len(pcm16_16k)
                offset = 0

                while total_len - offset >= frame_bytes:
                    chunk16k = pcm16_16k[offset : offset + frame_bytes]
                    offset += frame_bytes

                    # 如果 ctrl 捕获模式开启：这帧给 ctrl_frame_queue 做 VAD + 识别
                    if self._pause_mic_for_ctrl:
                        try:
                            self.ctrl_frame_queue.put_nowait(chunk16k)
                        except Exception:
                            # 队列如果出意外（理论上不太会），就丢帧，但不影响主对话
                            pass

                    # 决定是否把这帧发给大模型：
                    # 1）如果正在播放 TTS 且需要屏蔽麦克风；
                    # 2）或者处于 ctrl 捕获模式；
                    # -> 对大模型只发静音帧（间隔 self._silence_interval_sec）
                    if (
                        self.block_mic_while_playing and self._is_tts_playing()
                    ) or self._pause_mic_for_ctrl:
                        await self._send_silence_if_due()
                    else:
                        await self.client.task_request(chunk16k)

                # 采集一轮后 → 收话筒（独立 MIC 通道）
                self.send_mic_command("release_microphone")

                await asyncio.sleep(0.005)
            except Exception as e:
                print(f"读取麦克风数据出错: {e}")
                await asyncio.sleep(0.1)

    async def start(self) -> None:
        try:
            await self.client.connect()
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
            if not self.is_audio_file_input:
                self.audio_device.cleanup()


def save_input_pcm_to_wav(pcm_data: bytes, filename: str) -> None:
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(config.input_audio_config["channels"])
        wf.setsampwidth(2)
        wf.setframerate(config.input_audio_config["sample_rate"])
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


class VoiceDialogHandle:
    def __init__(
        self,
        thread: threading.Thread,
        loop: asyncio.AbstractEventLoop,
        stop_event: asyncio.Event,
    ):
        self._thread = thread
        self._loop = loop
        self._stop_event = stop_event

    def stop(self):
        if self._loop.is_closed():
            return

        def _set():
            if not self._stop_event.is_set():
                self._stop_event.set()

        self._loop.call_soon_threadsafe(_set)

    def join(self, timeout: Optional[float] = None):
        self._thread.join(timeout)


def _run_session_thread(
    start_prompt: str, audio_file_path: str, stop_event: asyncio.Event
):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = DialogSession(
        ws_config=config.ws_connect_config,
        start_prompt=start_prompt,
        output_audio_format="pcm",
        audio_file_path=audio_file_path,
    )
    session.attach_stop_event(stop_event)
    try:
        loop.run_until_complete(session.start())
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()


def start_voice_dialog(
    start_prompt: str, audio_file_path: str = ""
) -> VoiceDialogHandle:
    stop_event = asyncio.Event()
    loop = asyncio.new_event_loop()
    loop.close()
    thread = threading.Thread(
        target=_thread_entry,
        args=(start_prompt, audio_file_path, stop_event),
        daemon=True,
    )
    thread.start()
    placeholder_loop = asyncio.new_event_loop()
    handle = VoiceDialogHandle(
        thread=thread, loop=placeholder_loop, stop_event=stop_event
    )
    return handle


def _thread_entry(start_prompt: str, audio_file_path: str, stop_event: asyncio.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = DialogSession(
        ws_config=config.ws_connect_config,
        start_prompt=start_prompt,
        output_audio_format="pcm",
        audio_file_path=audio_file_path,
    )
    session.attach_stop_event(stop_event)
    try:
        loop.run_until_complete(session.start())
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()
