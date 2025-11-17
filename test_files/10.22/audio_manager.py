# audio_manager.py
import asyncio
import audioop  # 标准库：重采样
import functools  # ← 新增：用于 asyncio.to_thread 的兼容实现
import json
import os
import queue
import random
import re
import signal
import socket
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import pyaudio

import config
from realtime_dialog_client import RealtimeDialogClient

# --- 兼容 Python 3.8：为 asyncio.to_thread 提供后备实现 ---
try:
    _to_thread = asyncio.to_thread  # Python 3.9+
except AttributeError:
    async def _to_thread(func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))

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
TARGET_CHUNK_SAMPLES = 320


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
    def __init__(self, topic="/robot/speaker/audio", node_name="speaker_publisher",
                 queue_size=10, latched=False):
        if not _HAS_ROS1:
            raise RuntimeError("未检测到 ROS1 (rospy)。请在 ROS1 环境运行。")
        if not rospy.core.is_initialized():
            rospy.init_node(node_name, anonymous=True, disable_signals=True)
        self.topic = topic
        self._use_audio_msg = _HAS_AUDIO_DATA_MSG
        if self._use_audio_msg:
            self._pub = rospy.Publisher(topic, RosAudioData, queue_size=queue_size, latch=latched)
        else:
            self._pub = rospy.Publisher(topic, ByteMultiArray, queue_size=queue_size, latch=latched)
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

    def stop_stream(self): pass
    def close(self): self._closed = True


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

    def __init__(self, ws_config: Dict[str, Any], start_prompt: str,
                 output_audio_format: str = "pcm", audio_file_path: str = ""):
        self.start_prompt = start_prompt
        self.audio_file_path = audio_file_path
        self.is_audio_file_input = self.audio_file_path != ""
        if self.is_audio_file_input:
            self.quit_event = asyncio.Event()
        else:
            self.say_hello_over_event = asyncio.Event()

        self.session_id = str(uuid.uuid4())
        self.client = RealtimeDialogClient(
            config=ws_config, session_id=self.session_id, output_audio_format=output_audio_format
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

        # ★★ 新增/修改：MIC 通道独立化
        # 语音关键词：维持端口 5557（不变）
        self.voice_udp_host = "127.0.0.1"
        self.voice_udp_port = 5557
        self.voice_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # MIC 指令：新独立端口 5558
        self.mic_udp_host = "127.0.0.1"
        self.mic_udp_port = 5558
        self.mic_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # 关键词“挥手” 及去抖
        self._wave_re = re.compile(r"(挥手|揮手|招手|挥个手|挥下手)")
        self._kws_wave_cooldown = 1.5
        self._kws_wave_last_ts = 0.0

        # === 实时风格控制（来自 a.txt） ===
        self.control_path = Path(getattr(config, "runtime_control_path", "a.txt"))
        self._control_mtime = 0.0
        self._control_text = ""
        self._control_dirty = False
        self._ctrl_debounce_sec = 0.25   # 写文件的抖动去抖
        self._mute_until_ts = 0.0        # 控制更新后的“静音窗口”，丢弃模型确认回包

        # 后台任务：监控文件 + 机智地择机推送“静默风格更新”
        self._ctrl_watcher_task = asyncio.create_task(self._control_file_watcher())
        self._ctrl_apply_task   = asyncio.create_task(self._control_apply_loop())

        # 话筒指令冷却
        self._last_mic_send_time = 0.0

        if not self.is_audio_file_input and _HAS_ROS1:
            if not rospy.core.is_initialized():
                rospy.init_node("audio_manager_client", anonymous=True, disable_signals=True)
            self.remote_status_sub = rospy.Subscriber(
                self.remote_status_topic, Bool, self._remote_audio_status_callback, queue_size=10
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
            self.player_thread = threading.Thread(target=self._audio_player_thread, daemon=True)
            self.player_thread.start()

    # ---------- 新：独立的 MIC 指令发送 ----------
    def send_mic_command(self, command: str, cooldown_sec: float = 0.2):
        """
        向 MIC 专用端口(5558)发送指令：'send_microphone' 或 'release_microphone'
        与关键词通道(5557)彻底分离。
        """
        now = time.time()
        if now - self._last_mic_send_time < cooldown_sec:
            return
        try:
            msg = json.dumps({
                "type": "mic_command",
                "command": command,
                "timestamp": now
            })
            self.mic_udp_socket.sendto(msg.encode("utf-8"), (self.mic_udp_host, self.mic_udp_port))
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

    def attach_stop_event(self, evt: asyncio.Event) -> None:
        self.external_stop_event = evt

    def stop(self) -> None:
        self.is_recording = False
        self.is_playing = False
        self.is_running = False
        try:
            if hasattr(self, 'voice_udp_socket'):
                self.voice_udp_socket.close()
        except Exception:
            pass
        try:
            if hasattr(self, 'mic_udp_socket'):
                self.mic_udp_socket.close()
        except Exception:
            pass

        if self.is_audio_file_input:
            try:
                self.quit_event.set()
            except Exception:
                pass

        # 取消控制通道任务
        for t in (getattr(self, "_ctrl_watcher_task", None), getattr(self, "_ctrl_apply_task", None)):
            try:
                if t: t.cancel()
            except Exception:
                pass

        # 取消推广播报任务，避免悬挂
        try:
            if hasattr(self, "promote_task") and self.promote_task:
                self.promote_task.cancel()
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
        local_playing = (time.time() - self._last_play_ts) * 1000.0 < grace_ms or not self.audio_queue.empty()
        return local_playing or self.remote_playing

    def _emit_voice_keyword(self, keyword: str):
        try:
            now = time.time()
            msg = json.dumps({"type": "voice_keyword", "keyword": keyword, "timestamp": now})
            self.voice_udp_socket.sendto(msg.encode("utf-8"), (self.voice_udp_host, self.voice_udp_port))
            print(f"[KWS] 发送语音关键词：{keyword}")
        except Exception as e:
            print(f"[KWS] 发送UDP失败: {e}")

    def _maybe_emit_wave_from_asr(self, payload_msg: Dict[str, Any]):
        cand_texts = []
        for r in payload_msg.get("results", []):
            if r.get("text"): cand_texts.append(r["text"])
            for alt in r.get("alternatives", []):
                if alt.get("text"): cand_texts.append(alt["text"])
        extra = payload_msg.get("extra", {})
        if extra.get("origin_text"): cand_texts.append(extra["origin_text"])
        joined = " ".join(cand_texts)
        if not joined:
            return
        if "挥手" in joined or "挥一挥" in joined:
            self._emit_voice_keyword("wave")
        elif "点头" in joined:
            self._emit_voice_keyword("nod")
        elif "握手" in joined or "握一" in joined:
            self._emit_voice_keyword("shake")

    def handle_server_response(self, response: Dict[str, Any]) -> None:
        # === 静默控制窗口：丢弃模型对‘风格更新’的潜在确认性回包 ===
        try:
            now = time.time()
            if now < getattr(self, "_mute_until_ts", 0.0):
                # 丢 ACK 音频
                if response.get("message_type") == "SERVER_ACK":
                    return
                # 丢带文本的 FULL_RESPONSE
                if response.get("message_type") == "SERVER_FULL_RESPONSE":
                    print("[CTRL] 静默控制窗口：丢弃一次模型回复（避免‘好的我会…’打断）。")
                    return
        except Exception:
            pass

        if response == {}:
            return
        if response["message_type"] == "SERVER_ACK" and isinstance(response.get("payload_msg"), bytes):
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

            # 在 LLM 文本里找“左/右” → 走关键词通道(5557)
            if 'content' in payload_msg:
                content = payload_msg['content']
                if '左' in content:
                    try:
                        message = json.dumps({"type": "voice_keyword", "keyword": "left", "timestamp": time.time()})
                        self.voice_udp_socket.sendto(message.encode('utf-8'), (self.voice_udp_host, self.voice_udp_port))
                        print("检测到'左'关键词，发送UDP消息")
                    except Exception as e:
                        print(f"发送UDP消息失败: {e}")
                if '右' in content:
                    try:
                        message = json.dumps({"type": "voice_keyword", "keyword": "right", "timestamp": time.time()})
                        self.voice_udp_socket.sendto(message.encode('utf-8'), (self.voice_udp_host, self.voice_udp_port))
                        print("检测到'右'关键词，发送UDP消息")
                    except Exception as e:
                        print(f"发送UDP消息失败: {e}")

            if event == 451:
                try:
                    self._maybe_emit_wave_from_asr(payload_msg)
                except Exception as e:
                    print(f"[KWS] 解析ASR(451)失败: {e}")

            if event == 450:
                print(f"清空缓存音频: {response['session_id']}")
                while not self.audio_queue.empty():
                    try: self.audio_queue.get_nowait()
                    except queue.Empty: continue
                self.is_user_querying = True

            if (event == 350 and self.is_sending_chat_tts_text and
                payload_msg.get("tts_type") == "chat_tts_text"):
                while not self.audio_queue.empty():
                    try: self.audio_queue.get_nowait()
                    except queue.Empty: continue
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
        await self.client.chat_tts_text(self.is_user_querying, True, False, "这是第一轮TTS的开始和中间包事件，这两个合而为一了。")
        await self.client.chat_tts_text(self.is_user_querying, False, True, "这是第一轮TTS的结束事件。")
        await asyncio.sleep(10)
        await self.client.chat_tts_text(self.is_user_querying, True, False, "这是第二轮TTS的开始和中间包事件，这两个合而为一了。")
        await self.client.chat_tts_text(self.is_user_querying, False, True, "这是第二轮TTS的结束事件。")

    # ======== 实时风格控制：文件读取 / 监听 / 应用 ========
    async def _read_control_file(self) -> str:
        try:
            if not self.control_path.exists():
                return ""
            # 用兼容的 _to_thread 在线程池里读文件，避免阻塞事件循环
            return await _to_thread(self.control_path.read_text, encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[CTRL] 读取控制文件失败: {e}")
            return ""

    async def _control_file_watcher(self):
        """轮询 a.txt 的 mtime，有更新就读取到 _control_text 并置脏标记。"""
        while self.is_running:
            try:
                mtime = self.control_path.stat().st_mtime if self.control_path.exists() else 0.0
                if mtime and mtime != self._control_mtime:
                    # 去抖：等一会儿，防止半写状态
                    await asyncio.sleep(self._ctrl_debounce_sec)
                    text = (await self._read_control_file()).strip()
                    if text != self._control_text:
                        self._control_text = text
                        self._control_dirty = True
                        print(f"[CTRL] 捕获到新控制指令：{text!r}")
                    self._control_mtime = mtime
            except Exception as e:
                print(f"[CTRL] 监控失败: {e}")
            await asyncio.sleep(0.20)  # 200ms 轮询

    def _can_apply_control(self) -> bool:
        """选择‘安全时机’：不在播 TTS、用户不在说话时再推送静默控制。"""
        return self._control_dirty and (not self._is_tts_playing()) and (not self.is_user_querying)

    def _compose_control_payload(self) -> str:
        """
        生成给大模型的‘静默风格更新’文本：
        - 明确‘不要回复、不要确认、不要提及这段内容’
        - 仅在后续真正回答用户时生效
        """
        if not self._control_text:
            # 置空 = 恢复默认
            return ("[系统风格更新 - 静默]\n"
                    "从现在开始，恢复到默认的中性、自然、连贯的对话风格。"
                    "不要提及本段说明；不要回复本段的任何内容。")
        return (
            "[不可见问答或风格更新]\n"
            f"{self._control_text}\n"
            "执行要求：绝对不要在除本轮回答之外的任何输出中提及、复述或暗示存在‘系统风格更新’；"
            "只在后续用户输入时按上述风格作答。"
            "本条不并入既有上下文，仅作为后续轮次的风格与问题格式约束。"
            "回答必须延续上一轮用户话题，只依据用户可见消息作答；若与用户后续显式指令冲突，以用户为准。"
            "如果要让你提问题，则在下轮问答中提问，而不是在本轮回答里提问"
        )

    async def _send_control_update(self):
        """推送‘静默风格更新’，并在很短时间内静音丢弃可能的确认回包。"""
        try:
            payload = self._compose_control_payload()
            # 开一个短静音窗，丢弃可能出现的‘确认性’回包，避免打断正在进行的节奏
            self._mute_until_ts = time.time() + 1.2
            await self.client.chat_text_query(payload)
            self._control_dirty = False
            print("[CTRL] 已推送实时风格更新（静默）。")
        except Exception as e:
            print(f"[CTRL] 推送失败: {e}")

    async def _control_apply_loop(self):
        """在安全时机自动把最新控制指令以‘静默’方式下发。"""
        while self.is_running:
            if self._can_apply_control():
                await self._send_control_update()
            await asyncio.sleep(0.10)

    def _keyboard_signal(self, sig, frame):
        print(f"receive keyboard Ctrl+C")
        self.stop()

    async def receive_loop(self):
        try:
            while True:
                response = await self.client.receive_server_response()
                self.handle_server_response(response)
                if "event" in response and (response["event"] == 152 or response["event"] == 153):
                    print(f"receive session finished event: {response['event']}")
                    self.is_session_finished = True
                    break
                if self.is_audio_file_input and "event" in response and response["event"] == 359:
                    print(f"receive tts ended event")
                    self.is_session_finished = True
                    break
                if (not self.is_audio_file_input and "event" in response and
                    response["event"] == 359 and not self.say_hello_over_event.is_set()):
                    print(f"receive tts sayhello ended event")
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
            print(f"音频文件处理完成，等待服务器响应...")

    async def process_silence_audio(self) -> None:
        silence_data = b"\x00" * (TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES)
        await self.client.task_request(silence_data)

    def _resample_to_16k(self, pcm16_le_mono: bytes, in_rate: int) -> bytes:
        if in_rate == TARGET_SAMPLE_RATE:
            return pcm16_le_mono
        converted, self._ratecv_state = audioop.ratecv(
            pcm16_le_mono, TARGET_SAMPLE_WIDTH, TARGET_CHANNELS, in_rate, TARGET_SAMPLE_RATE, self._ratecv_state
        )
        return converted

    async def process_microphone_input(self) -> None:
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

                if self.block_mic_while_playing and self._is_tts_playing():
                    await self._send_silence_if_due()
                    await asyncio.sleep(0.01)
                    continue

                audio_data = stream.read(config.input_audio_config["chunk"], exception_on_overflow=False)
                mono = to_mono(audio_data)
                pcm16_16k = self._resample_to_16k(mono, in_rate)

                frame_bytes = TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES
                total_len = len(pcm16_16k)
                offset = 0
                while total_len - offset >= frame_bytes:
                    chunk16k = pcm16_16k[offset: offset + frame_bytes]
                    await self.client.task_request(chunk16k)
                    offset += frame_bytes

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
    def __init__(self, thread: threading.Thread, loop: asyncio.AbstractEventLoop, stop_event: asyncio.Event):
        self._thread = thread
        self._loop = loop
        self._stop_event = stop_event

    def stop(self):
        if self._loop.is_closed(): return
        def _set():
            if not self._stop_event.is_set():
                self._stop_event.set()
        self._loop.call_soon_threadsafe(_set)

    def join(self, timeout: Optional[float] = None):
        self._thread.join(timeout)


def _run_session_thread(start_prompt: str, audio_file_path: str, stop_event: asyncio.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = DialogSession(ws_config=config.ws_connect_config, start_prompt=start_prompt,
                            output_audio_format="pcm", audio_file_path=audio_file_path)
    session.attach_stop_event(stop_event)
    try:
        loop.run_until_complete(session.start())
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending: t.cancel()
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()


def start_voice_dialog(start_prompt: str, audio_file_path: str = "") -> VoiceDialogHandle:
    stop_event = asyncio.Event()
    loop = asyncio.new_event_loop()
    loop.close()
    thread = threading.Thread(target=_thread_entry, args=(start_prompt, audio_file_path, stop_event), daemon=True)
    thread.start()
    placeholder_loop = asyncio.new_event_loop()
    handle = VoiceDialogHandle(thread=thread, loop=placeholder_loop, stop_event=stop_event)
    return handle


def _thread_entry(start_prompt: str, audio_file_path: str, stop_event: asyncio.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = DialogSession(ws_config=config.ws_connect_config, start_prompt=start_prompt,
                            output_audio_format="pcm", audio_file_path=audio_file_path)
    session.attach_stop_event(stop_event)
    try:
        loop.run_until_complete(session.start())
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending: t.cancel()
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()