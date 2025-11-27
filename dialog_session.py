import asyncio
import audioop
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
from typing import Any, Dict, Optional, Set

import pyaudio

import config
from audio_constants import (
    ASR_KWS_PATTERNS,
    LLM_KWS_PATTERNS,
    TARGET_CHANNELS,
    TARGET_CHUNK_SAMPLES,
    TARGET_SAMPLE_RATE,
    TARGET_SAMPLE_WIDTH,
    AudioConfig,
)
from audio_device_manager import AudioDeviceManager
from realtime_dialog_client import RealtimeDialogClient
from sauc_python.sauc_websocket_mic2 import recognize_once_from_queue

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
    from std_msgs.msg import Bool, ByteMultiArray

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
    RosAudioData = None
    _HAS_ROS1 = False
    _HAS_AUDIO_DATA_MSG = False


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


class DialogSession:
    """
    负责：
      - 管理和大模型的 WebSocket 会话
      - 麦克风输入（ROS /audio/audio）与输出（PyAudio / ROS1 speaker）
      - ctrl.txt + SAUC 队列识别
      - 处理 LLM / ASR 关键词：UDP(5557)
      - 话筒收递：UDP(5558) —— FIX：改为“播放状态机”，只在状态变化时发一次
    """

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

        self._loop = asyncio.get_event_loop()

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

        # ---------- 状态 ----------
        self.is_running = True
        self.is_session_finished = False
        self.is_user_querying = False
        self.is_sending_chat_tts_text = False

        self.audio_buffer = b""
        self._ratecv_state = None

        # ---------- 播放状态（FIX：更稳的“本地播放保持窗口”） ----------
        self._local_play_hold_sec = 0.25  # 分片抖动时，保持“正在播放”的时间窗
        self._local_audio_deadline = 0.0  # time.time() < deadline => local playing
        self.remote_playing = False
        self.remote_status_topic = "/audio_playing_status"
        self.remote_status_sub = None

        # ---------- 语音上行静音填充 ----------
        self.block_mic_while_playing = True
        self._last_silence_ts = 0.0
        self._silence_interval_sec = 0.20

        self.external_stop_event: Optional[asyncio.Event] = None

        # ---------- UDP 通道 ----------
        self.voice_udp_host = "127.0.0.1"
        self.voice_udp_port = 5557
        self.voice_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.mic_udp_host = "127.0.0.1"
        self.mic_udp_port = 5558
        self.mic_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._last_mic_send_time = 0.0

        # ---------- mic 收递状态机（FIX：只在状态变化时发一次） ----------
        self._mic_state: Optional[str] = None  # "sent" / "released" / None
        self._handoff_last_playing: Optional[bool] = None
        self._handoff_change_ts = time.time()
        self._mic_handoff_task: Optional[asyncio.Task] = None

        # ---------- LLM 关键词缓冲 ----------
        self._llm_keyword_buffer: str = ""
        self._llm_buffer_max_len: int = 50
        self._llm_kws_fired: Set[str] = set()

        # ---------- ctrl.txt + SAUC ----------
        self._pause_mic_for_ctrl = False
        self.ctrl_frame_queue: asyncio.Queue = asyncio.Queue()

        self.sauc_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "sauc_python"
        )
        self.ctrl_file_path = os.path.join(self.sauc_dir, "ctrl.txt")
        self.output_file_path = os.path.join(self.sauc_dir, "output.txt")

        self._last_ctrl_text: Optional[str] = None
        self._ctrl_pending_text: Optional[str] = None
        self._ctrl_worker_task: Optional[asyncio.Task] = None
        self.ctrl_monitor_task: Optional[asyncio.Task] = None

        # ---------- 推销任务 ----------
        self._last_promote_ts = 0
        self._promote_playing = False
        self.promote_task = self._loop.create_task(self._promote_task())

        # ---------- ROS 麦克风订阅 ----------
        self.ros_audio_queue: Optional[queue.Queue] = None
        self.ros_audio_sub = None

        if (not self.is_audio_file_input) and _HAS_ROS1:
            if not rospy.core.is_initialized():
                rospy.init_node(
                    "audio_manager_client", anonymous=True, disable_signals=True
                )

            # 播放状态订阅
            self.remote_status_sub = rospy.Subscriber(
                self.remote_status_topic,
                Bool,
                self._remote_audio_status_callback,
                queue_size=10,
            )
            print(f"已订阅下位机播放状态话题: {self.remote_status_topic}")

            # 麦克风音频订阅
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

        # ---------- 播放线程 ----------
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

            # ctrl 监控
            self.ctrl_monitor_task = self._loop.create_task(self._monitor_ctrl_file())

            # FIX：启动 mic 收递状态机任务
            self._mic_handoff_task = self._loop.create_task(self._mic_handoff_loop())

        # signal 只能在主线程设置，避免你用 thread 跑时直接炸
        try:
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGINT, self._keyboard_signal)
        except Exception:
            pass

    # ---------- ROS 麦克风回调 ----------
    def _ros_audio_callback(self, msg: "RosAudioData"):
        if self.ros_audio_queue is None:
            return
        try:
            pcm_bytes = bytes(msg.data)
            try:
                self.ros_audio_queue.put_nowait(pcm_bytes)
            except queue.Full:
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

    # ---------- FIX：基于“播放状态变化”的话筒收递状态机 ----------
    async def _mic_handoff_loop(self):
        """
        目标逻辑：
          - 机器人开始说话(playing=True) -> release_microphone（只发一次）
          - 机器人说完(playing=False稳定一段时间) -> send_microphone（只发一次）
        防抖：
          - playing=True 持续 >= 0.10s 才触发 release
          - playing=False 持续 >= 0.35s 才触发 send
        """
        self._mic_state = None
        self._handoff_last_playing = None
        self._handoff_change_ts = time.time()

        while self.is_running:
            try:
                playing = self._is_tts_playing()

                now = time.time()
                if (
                    self._handoff_last_playing is None
                    or playing != self._handoff_last_playing
                ):
                    self._handoff_last_playing = playing
                    self._handoff_change_ts = now

                stable_sec = now - self._handoff_change_ts

                if playing:
                    # 机器人在说话 -> 收话筒
                    if self._mic_state != "sent" and stable_sec >= 0.02:
                        self.send_mic_command("send_microphone")
                        self._mic_state = "sent"
                    # if self._mic_state != "released" and stable_sec >= 0.10:
                    #     self.send_mic_command("release_microphone")
                    #     self._mic_state = "released"
                else:
                    # # 机器人不说话 -> 递话筒
                    # if self._mic_state != "sent" and stable_sec >= 0.35:
                    #     self.send_mic_command("send_microphone")
                    #     self._mic_state = "sent"
                    if self._mic_state != "released" and stable_sec >= 0.12:
                        self.send_mic_command("release_microphone")
                        self._mic_state = "released"

                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[MIC-HANDOFF] 异常: {e}")
                await asyncio.sleep(0.1)

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

    # ---------- ctrl.txt 监控 ----------
    async def _monitor_ctrl_file(self):
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
                    await asyncio.sleep(0.5)
                    continue

                if self._last_ctrl_text is None:
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
        try:
            print(f"[CTRL-MONITOR] 检测到 ctrl.txt 内容变化，内容: {ctrl_text!r}")
            self._ctrl_pending_text = ctrl_text

            if self._ctrl_worker_task is not None and not self._ctrl_worker_task.done():
                print(
                    "[CTRL-MONITOR] 已有 ctrl worker 在运行，更新 pending 文本后等待其完成。"
                )
                return

            self._ctrl_worker_task = asyncio.create_task(self._ctrl_utterance_worker())
            print("[CTRL-MONITOR] 启动 ctrl worker，等待下一轮自然说话。")
        except Exception as e:
            print(f"[CTRL-MONITOR] 处理 ctrl 变化失败: {e}")

    async def _ctrl_utterance_worker(self):
        try:
            initial_ctrl_text = self._ctrl_pending_text
            if not initial_ctrl_text:
                print("[CTRL-WORKER] 启动时 ctrl_pending_text 为空，直接退出。")
                return

            print(f"[CTRL-WORKER] 启动，ctrl_text={initial_ctrl_text!r}")

            attempt = 0
            while self.is_running and self._ctrl_pending_text:
                attempt += 1
                print(f"[CTRL-WORKER] 第 {attempt} 次 ctrl 语音捕获尝试...")

                while self.is_running:
                    if (not self.is_user_querying) and (not self._is_tts_playing()):
                        break
                    await asyncio.sleep(0.1)

                if not self.is_running:
                    return

                try:
                    while not self.ctrl_frame_queue.empty():
                        self.ctrl_frame_queue.get_nowait()
                except Exception:
                    pass

                self._pause_mic_for_ctrl = True
                print("[CTRL-WORKER] 启用 ctrl 捕获模式，等待用户下一句...")

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
                    print(f"[CTRL-WORKER] SAUC 失败: {e}")
                    asr_text = ""
                finally:
                    self._pause_mic_for_ctrl = False
                    try:
                        while not self.ctrl_frame_queue.empty():
                            self.ctrl_frame_queue.get_nowait()
                    except Exception:
                        pass

                print(f"[CTRL-WORKER] SAUC 结果：{asr_text!r}")

                if not asr_text:
                    print("[CTRL-WORKER] 未检测到有效语音，继续挂起等待下一轮。")
                    await asyncio.sleep(0.2)
                    continue

                ctrl_text_final = self._ctrl_pending_text or initial_ctrl_text
                pieces = []
                if ctrl_text_final:
                    pieces.append(ctrl_text_final)
                pieces.append(asr_text)
                combined_text = " ".join(pieces)

                print(f"[CTRL-WORKER] 发送合并文本到大模型：{combined_text!r}")
                try:
                    await self.client.chat_text_query(combined_text)
                except Exception as e:
                    print(f"[CTRL-WORKER] chat_text_query 发送失败: {e}")

                self._ctrl_pending_text = None
                print("[CTRL-WORKER] ctrl 已消耗，结束。")
                return

        except asyncio.CancelledError:
            print("[CTRL-WORKER] 被取消")
            raise
        except Exception as e:
            print(f"[CTRL-WORKER] 异常: {e}")
        finally:
            self._pause_mic_for_ctrl = False
            try:
                while not self.ctrl_frame_queue.empty():
                    self.ctrl_frame_queue.get_nowait()
            except Exception:
                pass

    def _remote_audio_status_callback(self, msg):
        self.remote_playing = bool(msg.data)

    def attach_stop_event(self, evt: asyncio.Event) -> None:
        self.external_stop_event = evt

    def stop(self) -> None:
        self.is_recording = False
        self.is_playing = False
        self.is_running = False

        try:
            self.voice_udp_socket.close()
        except Exception:
            pass
        try:
            self.mic_udp_socket.close()
        except Exception:
            pass

        try:
            if self.ros_audio_sub is not None:
                self.ros_audio_sub.unregister()
        except Exception:
            pass

        if self.is_audio_file_input:
            try:
                self.quit_event.set()
            except Exception:
                pass

        for tname in [
            "promote_task",
            "ctrl_monitor_task",
            "_ctrl_worker_task",
            "_mic_handoff_task",
        ]:
            try:
                t = getattr(self, tname, None)
                if t is not None:
                    t.cancel()
            except Exception:
                pass

    def _audio_player_thread(self):
        """
        FIX：
          - 这里只负责“把音频写出去” + 更新 local_audio_deadline
          - 不再在这里 send_microphone（否则分片会导致频繁递话筒）
        """
        while self.is_playing:
            try:
                audio_data = self.audio_queue.get(timeout=1.0)
                if audio_data is not None:
                    self.output_stream.write(audio_data)
                    self._local_audio_deadline = time.time() + self._local_play_hold_sec
            except queue.Empty:
                time.sleep(0.05)
            except Exception as e:
                print(f"音频播放错误: {e}")
                time.sleep(0.1)

    def _is_tts_playing(self) -> bool:
        # local: deadline 或 queue 非空
        now = time.time()
        local_playing = (now < self._local_audio_deadline) or (
            not self.audio_queue.empty()
        )
        # remote: 下位机状态
        return bool(local_playing or self.remote_playing)

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

        for keyword, patterns in ASR_KWS_PATTERNS.items():
            if any(p in joined for p in patterns):
                self._emit_voice_keyword(keyword)
                print(f"[ASR-KWS] 检测到关键词 '{keyword}', 已发送 UDP")
                break

    def handle_server_response(self, response: Dict[str, Any]) -> None:
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

            if event == 553:
                self._llm_keyword_buffer = ""
                self._llm_kws_fired.clear()

            if "content" in payload_msg:
                content = payload_msg["content"]
                self._llm_keyword_buffer += content
                if len(self._llm_keyword_buffer) > self._llm_buffer_max_len:
                    self._llm_keyword_buffer = self._llm_keyword_buffer[
                        -self._llm_buffer_max_len :
                    ]

                buf = self._llm_keyword_buffer
                for keyword, patterns in LLM_KWS_PATTERNS.items():
                    if keyword in self._llm_kws_fired:
                        continue
                    if any(p in buf for p in patterns):
                        self._emit_voice_keyword(keyword)
                        print(f"[LLM-KWS] 检测到关键词 '{keyword}', 已发送 UDP")
                        self._llm_kws_fired.add(keyword)
                        if keyword == "end":
                            self._llm_keyword_buffer = ""

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
                    (not self.is_audio_file_input)
                    and "event" in response
                    and response["event"] == 359
                    and (not self.say_hello_over_event.is_set())
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
            await asyncio.sleep(0.01)
            if self.quit_event.is_set():
                break
            await self.process_silence_audio()

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
        从 ROS /audio/audio 读取麦克风数据，切帧上传给大模型。
        FIX：不再在这里 release_microphone（否则每个音频块都会“收一下”）
        收递由 _mic_handoff_loop 统一根据播放状态完成。
        """
        await self.client.say_hello()
        await self.say_hello_over_event.wait()
        await self.client.chat_text_query(self.start_prompt)

        in_rate = config.input_audio_config["sample_rate"]
        in_channels = config.input_audio_config["channels"]
        in_width = 2

        print(
            f"使用 ROS /audio/audio 作为麦克风输入，采样率={in_rate}Hz, channels={in_channels}"
        )

        def to_mono(pcm_bytes: bytes) -> bytes:
            if in_channels == 1:
                return pcm_bytes
            try:
                return audioop.tomono(pcm_bytes, in_width, 0.5, 0.5)
            except Exception as e:
                print(f"[ROS-MIC] tomono 失败，直接用原始音频: {e}")
                return pcm_bytes

        frame_bytes = TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES

        while self.is_recording:
            try:
                if self.external_stop_event and self.external_stop_event.is_set():
                    self.stop()
                    break

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

                total_len = len(pcm16_16k)
                offset = 0

                while total_len - offset >= frame_bytes:
                    chunk16k = pcm16_16k[offset : offset + frame_bytes]
                    offset += frame_bytes

                    if self._pause_mic_for_ctrl:
                        try:
                            self.ctrl_frame_queue.put_nowait(chunk16k)
                        except Exception:
                            pass

                    if (
                        self.block_mic_while_playing and self._is_tts_playing()
                    ) or self._pause_mic_for_ctrl:
                        await self._send_silence_if_due()
                    else:
                        await self.client.task_request(chunk16k)

                await asyncio.sleep(0.005)

            except Exception as e:
                print(f"从 ROS 队列读取麦克风数据出错: {e}")
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


def start_voice_dialog(
    start_prompt: str, audio_file_path: str = ""
) -> VoiceDialogHandle:
    stop_event = asyncio.Event()

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
