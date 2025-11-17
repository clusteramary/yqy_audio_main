# audio_manager.py
import asyncio
import audioop  # 标准库：重采样
import json
import queue
import random
import re  # ★ 新增：用于匹配“挥手”关键词
import signal
import socket
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pyaudio

import config
from realtime_dialog_client import RealtimeDialogClient

# 主动推销：await asyncio.sleep(20)  # 每40秒检查一次

# print(f"[UDP] 冷却时间未到，跳过发送指令：{command}")

# ======= 在文件顶部现有 import 下方，追加这段（ROS 依赖是可选）=======
try:
    import rospy
    from std_msgs.msg import Bool, ByteMultiArray  # 添加 Bool 消息类型

    # 优先用 audio_common_msgs/AudioData（若已安装）
    try:
        from audio_common_msgs.msg import AudioData as RosAudioData

        _HAS_AUDIO_DATA_MSG = True
    except Exception:
        _HAS_AUDIO_DATA_MSG = False
    _HAS_ROS1 = True
except Exception:
    _HAS_ROS1 = False
    _HAS_AUDIO_DATA_MSG = False
# ===============================================================


# 统一定义目标发送采样率（与静音包一致）
TARGET_SAMPLE_RATE = 16000  # 发往服务端的采样率
TARGET_SAMPLE_WIDTH = 2  # 16-bit
TARGET_CHANNELS = 1  # 单声道
TARGET_CHUNK_SAMPLES = 320  # 10ms @ 16k（与你的静音帧一致）


@dataclass
class AudioConfig:
    """音频配置数据类"""

    format: str
    bit_size: int
    channels: int
    sample_rate: int
    chunk: int
    device_index: Optional[int] = None  # 输入/输出设备索引（PyAudio 模式有效）

    # ↓↓↓ 仅输出端会用到的可选字段；不填时仍维持 PyAudio 本地播放 ↓↓↓
    mode: str = "pyaudio"  # 'pyaudio' 或 'ros1'
    ros1_topic: str = "/robot/speaker/audio"
    ros1_node_name: str = "speaker_publisher"
    ros1_queue_size: int = 10
    ros1_latch: bool = False  # 音频流不建议上锁定（latched），默认 False


# ==================== 新增：ROS1 扬声器"流"包装 ====================
class Ros1SpeakerStream:
    """
    提供与 PyAudio Stream 相同的接口：write()/stop_stream()/close()
    内部把 PCM S16LE 字节发布到 ROS1 话题，供下位机扬声器节点播放。
    """

    def __init__(
        self,
        topic: str = "/robot/speaker/audio",
        node_name: str = "speaker_publisher",
        queue_size: int = 10,
        latched: bool = False,
    ):
        if not _HAS_ROS1:
            raise RuntimeError("未检测到 ROS1 (rospy)。请安装并在 ROS1 环境中运行。")

        # 若未初始化，则在此初始化。禁用信号由上层掌控中断。
        if not rospy.core.is_initialized():
            rospy.init_node(node_name, anonymous=True, disable_signals=True)

        self.topic = topic
        self._use_audio_msg = (
            _HAS_AUDIO_DATA_MSG  # 优先使用 audio_common_msgs/AudioData
        )
        if self._use_audio_msg:
            self._pub = rospy.Publisher(
                topic, RosAudioData, queue_size=queue_size, latch=latched
            )
            rospy.loginfo(f"[Ros1SpeakerStream] Publishing AudioData on {topic}")
        else:
            self._pub = rospy.Publisher(
                topic, ByteMultiArray, queue_size=queue_size, latch=latched
            )
            rospy.logwarn(
                f"[Ros1SpeakerStream] audio_common_msgs/AudioData 不可用，改用 ByteMultiArray 发送：{topic}"
            )

        self._closed = False

    def write(self, audio_bytes: bytes):
        """与 PyAudio Stream.write 接口一致——逐块发送音频"""
        if self._closed:
            return
        if self._use_audio_msg:
            msg = RosAudioData()
            # audio_common_msgs/AudioData 的字段名即 data (uint8[])
            msg.data = list(audio_bytes)  # 或者 bytes -> list[int]
        else:
            msg = ByteMultiArray()
            # ByteMultiArray 的 data 也是 uint8[]
            msg.data = list(audio_bytes)
        self._pub.publish(msg)

    def stop_stream(self):
        """保持接口一致；ROS 发布器无需显式 stop"""
        pass

    def close(self):
        """保持接口一致；标记为关闭即可"""
        self._closed = True


# ===============================================================


# =================== 修改：AudioDeviceManager ===================
class AudioDeviceManager:
    """音频设备管理类，处理音频输入输出"""

    def __init__(self, input_config: AudioConfig, output_config: AudioConfig):
        self.input_config = input_config
        self.output_config = output_config
        self.pyaudio = pyaudio.PyAudio()
        self.input_stream: Optional[pyaudio.Stream] = None

        # output_stream 可能是 PyAudio Stream 或 Ros1SpeakerStream（都带 write/close 接口）
        self.output_stream: Optional[Any] = None

    def open_input_stream(self) -> pyaudio.Stream:
        """打开音频输入流（麦克风直连）"""
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
        """
        打开音频输出"流"：
        - mode='pyaudio'：返回 PyAudio 的 Stream（原逻辑不变）
        - mode='ros1'   ：返回 Ros1SpeakerStream（publish 到 ROS 话题）
        """
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
            # 直接构建一个具有 .write() 的"流"，上层无需改动
            self.output_stream = Ros1SpeakerStream(
                topic=self.output_config.ros1_topic,
                node_name=self.output_config.ros1_node_name,
                queue_size=self.output_config.ros1_queue_size,
                latched=self.output_config.ros1_latch,
            )
            return self.output_stream

        else:
            raise ValueError(f"未知输出模式：{mode!r}，应为 'pyaudio' 或 'ros1'")

    def cleanup(self) -> None:
        """清理音频设备资源"""
        # 关闭输入流
        if isinstance(self.input_stream, pyaudio.Stream):
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception:
                pass
        self.input_stream = None

        # 关闭输出流：兼容 PyAudio Stream & Ros1SpeakerStream
        if self.output_stream is not None:
            try:
                # 两类都支持 stop_stream/close（Ros1SpeakerStream 为空实现）
                if hasattr(self.output_stream, "stop_stream"):
                    self.output_stream.stop_stream()
                if hasattr(self.output_stream, "close"):
                    self.output_stream.close()
            except Exception:
                pass
        self.output_stream = None

        # 仅当用到 PyAudio 时才 terminate
        try:
            self.pyaudio.terminate()
        except Exception:
            pass


# ===============================================================


class DialogSession:
    """对话会话管理类"""

    is_audio_file_input: bool

    def __init__(
        self,
        ws_config: Dict[str, Any],
        start_prompt: str,  # ← 新增：外部传入的首轮 prompt
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

        # 新增的推销内容播放逻辑
        self._last_promote_ts = 0  # 上次推销内容播放时间戳
        self.promote_task = asyncio.create_task(self._promote_task())  # 定时推销任务
        self._promote_playing = False  # 添加标志位，防止重复播放

        self.is_running = True
        self.is_session_finished = False
        self.is_user_querying = False
        self.is_sending_chat_tts_text = False
        self.audio_buffer = b""

        # 重采样跨帧状态
        self._ratecv_state = None

        # 半双工防回声
        self._last_play_ts = 0.0  # 最近一次向扬声器写入的时间戳
        self.block_mic_while_playing = True  # 播放TTS时阻断麦克风上传

        # === 新增：静音保活的定时器（尽量少发，默认每 200ms 发一帧 10ms 的静音） ===
        self._last_silence_ts = 0.0  # NEW
        self._silence_interval_sec = 0.20  # NEW，可按需调 0.10~0.50
        # ======================================================================

        # 外部停止事件（可注入）
        self.external_stop_event: Optional[asyncio.Event] = None

        # ============ 新增：远程播放状态跟踪 ============
        self.remote_playing = False  # 下位机是否正在播放
        self.remote_status_topic = "/audio_playing_status"  # 下位机状态话题
        self.remote_status_sub = None  # 状态订阅器

        # 新增UDP发送配置
        self.voice_udp_host = "127.0.0.1"  # 发送到接收端
        self.voice_udp_port = 5557  # 使用不同端口避免冲突
        self.voice_udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # ★ 新增：本地“挥手”关键词检测的正则与去抖
        self._wave_re = re.compile(r"(挥手|揮手|招手|挥个手|挥下手)")
        self._kws_wave_cooldown = 1.5  # 秒
        self._kws_wave_last_ts = 0.0
        
        self._last_send_time = 0  # 记录最后一次发送的时间


        # 如果不是音频文件输入模式，初始化ROS并订阅远程状态
        if not self.is_audio_file_input and _HAS_ROS1:
            # 确保ROS节点已初始化
            if not rospy.core.is_initialized():
                rospy.init_node("audio_manager_client", anonymous=True, disable_signals=True)

            # 订阅下位机播放状态
            self.remote_status_sub = rospy.Subscriber(
                self.remote_status_topic,
                Bool,
                self._remote_audio_status_callback,
                queue_size=10
            )
            print(f"已订阅下位机播放状态话题: {self.remote_status_topic}")
        # ===============================================

        signal.signal(signal.SIGINT, self._keyboard_signal)
        self.audio_queue = queue.Queue()
        if not self.is_audio_file_input:
            self.audio_device = AudioDeviceManager(
                AudioConfig(**config.input_audio_config),
                AudioConfig(**config.output_audio_config),
            )
            # 初始化音频队列和输出流
            self.output_stream = self.audio_device.open_output_stream()
            # 启动播放线程
            self.is_recording = True
            self.is_playing = True
            self.player_thread = threading.Thread(
                target=self._audio_player_thread, daemon=True
            )
            self.player_thread.start()

    def send_udp_message(self, command: str):
        """发送UDP消息，添加冷却时间，避免频繁发送"""
        current_time = time.time()
        if current_time - self._last_send_time < 1.0:  # 1秒冷却时间
            # print(f"[UDP] 冷却时间未到，跳过发送指令：{command}")
            return  # 如果距离上次发送时间不到1秒，跳过这次发送

        try:
            now = current_time
            msg = json.dumps({
                "type": "mic_command",  # 修改为 mic_command 类型
                "command": command,  # 递话筒或收话筒命令
                "timestamp": now
            })
            self.voice_udp_socket.sendto(msg.encode("utf-8"), (self.voice_udp_host, self.voice_udp_port))
            print(f"[UDP] 发送指令：{command}")
            self._last_send_time = now  # 更新最后发送时间
        except Exception as e:
            print(f"[UDP] 发送失败: {e}")
    
    async def _promote_task(self):
        """定时每40秒推送推销内容"""
        while self.is_running:
            await asyncio.sleep(2000000)  # 每40秒检查一次（此处按你原值保留）
            if not self._is_tts_playing() and not self._promote_playing:
                await self._play_promote_message()
            else:
                while self._is_tts_playing():
                    await asyncio.sleep(1)
                await self._play_promote_message()

    async def _play_promote_message(self):
        """播放推销内容"""
        if self._promote_playing:
            return  # 如果已经在播放推销内容，避免重复播放

        self._promote_playing = True  # 标记开始播放推销内容

        promote_message1 = "哦对了，欢迎了解华科智能机器人。"
        promote_message2 = "请扫旁边的二维码加入群聊。"
        print(f"开始播放推销内容: {promote_message1}")
        await self.client.chat_tts_text(
            is_user_querying=False,
            start=True,
            end=False,
            content=promote_message1,
        )

        await self.client.chat_tts_text(
            is_user_querying=False,
            start=False,
            end=True,
            content=promote_message2,
        )
        self._last_promote_ts = time.time()
        print("推销内容播放完毕")

        self._promote_playing = False  # 播放完毕，重置标志位

    def _remote_audio_status_callback(self, msg):
        """下位机播放状态回调"""
        self.remote_playing = msg.data

    # ------- 对外接口 -------
    def attach_stop_event(self, evt: asyncio.Event) -> None:
        """允许外部注入 stop 事件"""
        self.external_stop_event = evt

    # 在stop方法中关闭socket
    def stop(self) -> None:
        """同步上下文里直接请求停止"""
        self.is_recording = False
        self.is_playing = False
        self.is_running = False
        # 关闭UDP socket
        if hasattr(self, 'voice_udp_socket'):
            self.voice_udp_socket.close()
        if self.is_audio_file_input:
            try:
                self.quit_event.set()
            except Exception:
                pass

    # ------------------------

    def _audio_player_thread(self):
        """音频播放线程"""
        while self.is_playing:
            try:
                audio_data = self.audio_queue.get(timeout=1.0)
                if audio_data is not None:
                    self.output_stream.write(audio_data)
                    self._last_play_ts = time.time()  # 记录播放时间戳
                    
                    # 播放完毕后发送"递话筒"消息
                    self.send_udp_message("send_microphone")  # 递话筒  
            except queue.Empty:
                time.sleep(0.1)
            except Exception as e:
                print(f"音频播放错误: {e}")
                time.sleep(0.1)

    def _is_tts_playing(self, grace_ms: float = 150.0) -> bool:
        """检查TTS是否正在播放（本地或远程）"""
        # 本地播放状态：最近 grace_ms 毫秒内写过或队列非空
        local_playing = (
            time.time() - self._last_play_ts
        ) * 1000.0 < grace_ms or not self.audio_queue.empty()

        # 返回本地或远程任一播放状态
        return local_playing or self.remote_playing

    # ★ 新增：统一发送语音关键词的工具
    def _emit_voice_keyword(self, keyword: str):
        """按既有'左/右'格式发UDP：{'type':'voice_keyword','keyword':..., 'timestamp':...}"""
        try:
            now = time.time()
            msg = json.dumps({
                "type": "voice_keyword",
                "keyword": keyword,
                "timestamp": now
            })
            self.voice_udp_socket.sendto(msg.encode("utf-8"), (self.voice_udp_host, self.voice_udp_port))
            print(f"[KWS] 发送语音关键词：{keyword}")
        except Exception as e:
            print(f"[KWS] 发送UDP失败: {e}")

    # ★ 新增：在流式 ASR 的 payload 里检测“挥手”
    def _maybe_emit_wave_from_asr(self, payload_msg: Dict[str, Any]):
        """
        在 ASR(451) 的 payload_msg 中找 '挥手' 等词，命中则发 'wave'（索引7由接收端映射）。
        做 1.5s 去抖，避免中间结果重复触发。
        """
        cand_texts = []

        # 1) results[*].text
        for r in payload_msg.get("results", []):
            t = r.get("text")
            if t:
                cand_texts.append(t)
            # 2) results[*].alternatives[*].text
            for alt in r.get("alternatives", []):
                at = alt.get("text")
                if at:
                    cand_texts.append(at)

        # 3) extra.origin_text（有些ASR实现会把原始拼接放这里）
        extra = payload_msg.get("extra", {})
        origin_text = extra.get("origin_text")
        if origin_text:
            cand_texts.append(origin_text)

        # 聚合后检测关键字
        joined = " ".join(cand_texts)
        if not joined:
            return

        # if self._wave_re.search(joined):
        #     now = time.time()
        #     if now - self._kws_wave_last_ts >= self._kws_wave_cooldown:
        #         self._kws_wave_last_ts = now
        #         self._emit_voice_keyword("wave")
        if "挥手" in joined or "挥一挥" in joined:  # 握手检测
            now = time.time()
            self._emit_voice_keyword("wave")
        elif "点头" in joined:  # 点头检测
            now = time.time()
            self._emit_voice_keyword("nod")

    def handle_server_response(self, response: Dict[str, Any]) -> None:
        if response == {}:
            return
        """处理服务器响应"""
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

            # A. 原有：在 LLM 文本 content 里找“左/右”
            if 'content' in payload_msg:
                content = payload_msg['content']
                # 检查关键词并发送UDP消息
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

            # B. ★ 新增：在流式 ASR (event == 451) 中找“挥手” → 发送 'wave'
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

    # 其他代码保持不变...

    async def _send_silence_if_due(self):  # NEW
        """在播放期保活：到时间就发一帧 10ms@16k 静音，避免频繁打满带宽。"""
        now = time.time()
        if (now - self._last_silence_ts) >= self._silence_interval_sec:
            await self.process_silence_audio()  # 发送一帧 10ms 静音
            self._last_silence_ts = now

    async def trigger_chat_tts_text(self):
        """概率触发发送ChatTTSText请求"""
        print("hit ChatTTSText event, start sending...")
        await self.client.chat_tts_text(
            is_user_querying=self.is_user_querying,
            start=True,
            end=False,
            content="这是第一轮TTS的开始和中间包事件，这两个合而为一了。",
        )
        await self.client.chat_tts_text(
            is_user_querying=self.is_user_querying,
            start=False,
            end=True,
            content="这是第一轮TTS的结束事件。",
        )
        await asyncio.sleep(10)
        await self.client.chat_tts_text(
            is_user_querying=self.is_user_querying,
            start=True,
            end=False,
            content="这是第二轮TTS的开始和中间包事件，这两个合而为一了。",
        )
        await self.client.chat_tts_text(
            is_user_querying=self.is_user_querying,
            start=False,
            end=True,
            content="这是第二轮TTS的结束事件。",
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
                    print(f"receive tts ended event")
                    self.is_session_finished = True
                    break
                if (
                    not self.is_audio_file_input
                    and "event" in response
                    and response["event"] == 359
                    and not self.say_hello_over_event.is_set()
                ):
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
                await self.process_silence_audio()  # 16k/10ms
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
        """发送静音音频（16k/10ms）"""
        silence_data = b"\x00" * (TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES)
        await self.client.task_request(silence_data)

    def _resample_to_16k(self, pcm16_le_mono: bytes, in_rate: int) -> bytes:
        """使用 audioop.ratecv 将 16-bit 单声道 PCM 从 in_rate 重采样到 16k（带状态）"""
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
        await self.client.say_hello()
        await self.say_hello_over_event.wait()
        # 用外部传入的首轮 prompt
        await self.client.chat_text_query(self.start_prompt)

        stream = self.audio_device.open_input_stream()
        print(
            f"已打开麦克风（device_index={config.input_audio_config.get('device_index')}），"
            f"采样率={config.input_audio_config['sample_rate']}，chunk={config.input_audio_config['chunk']}，开始讲话..."
        )

        in_rate = config.input_audio_config["sample_rate"]
        in_channels = config.input_audio_config["channels"]
        in_width = 2  # paInt16 = 2 bytes

        def to_mono(pcm_bytes: bytes) -> bytes:
            """多声道下混为单声道"""
            if in_channels == 1:
                return pcm_bytes
            return audioop.tomono(pcm_bytes, in_width, 0.5, 0.5)

        while self.is_recording:
            try:
                if self.external_stop_event and self.external_stop_event.is_set():
                    self.stop()
                    break

                # 半双工：播放TTS时不读不发，但增加保活静音，防止服务器断开
                if self.block_mic_while_playing and self._is_tts_playing():
                    await self._send_silence_if_due()   # NEW：到点发静音做 keep-alive
                    await asyncio.sleep(0.01)
                    continue

                audio_data = stream.read(
                    config.input_audio_config["chunk"], exception_on_overflow=False
                )
                mono = to_mono(audio_data)
                pcm16_16k = self._resample_to_16k(mono, in_rate)

                # 按 10ms@16k 切包发送（320 样本 * 2字节）
                frame_bytes = TARGET_SAMPLE_WIDTH * TARGET_CHUNK_SAMPLES
                total_len = len(pcm16_16k)
                offset = 0
                while total_len - offset >= frame_bytes:
                    chunk16k = pcm16_16k[offset : offset + frame_bytes]
                    await self.client.task_request(chunk16k)
                    offset += frame_bytes

                # 发送"收话筒"消息
                self.send_udp_message("release_microphone")  # 收话筒

                await asyncio.sleep(0.005)
            except Exception as e:
                print(f"读取麦克风数据出错: {e}")
                await asyncio.sleep(0.1)

    async def start(self) -> None:
        """启动对话会话（支持外部 stop_event）"""
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

                # 主循环：同时监听 is_running 与 external_stop_event
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
    """保存PCM数据为WAV文件（按输入采样率保存，用于诊断）"""
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(config.input_audio_config["channels"])
        wf.setsampwidth(2)  # paInt16 = 2 bytes
        wf.setframerate(config.input_audio_config["sample_rate"])
        wf.writeframes(pcm_data)


def save_output_to_file(audio_data: bytes, filename: str) -> None:
    """保存原始PCM音频数据到文件"""
    if not audio_data:
        print("No audio data to save.")
        return
    try:
        with open(filename, "wb") as f:
            f.write(audio_data)
    except IOError as e:
        print(f"Failed to save pcm file: {e}")
        



# ============ 对外封装：函数式启动 / 停止 ============


class VoiceDialogHandle:
    """
    返回给调用方的句柄：
      - stop(): 请求优雅停止
      - join(timeout=None): 等待结束（可选）
    """

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
        """外部停止：线程安全"""
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
    """在线程中启动事件循环并运行 DialogSession"""
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
        # 保证 loop 关闭
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
    """
    对外主入口（同步环境也可用）：
      - 传入首轮 prompt
      - 返回 handle，可随时 handle.stop() 终止
    """
    stop_event = asyncio.Event()
    # 先创建一个"临时loop"仅用于构造 Event 的底层 Future；随后在线程里会重新设 loop
    loop = asyncio.new_event_loop()
    loop.close()  # 只是为了确保 stop_event 构造不依赖当前线程loop

    # 真正运行的线程
    thread = threading.Thread(
        target=_thread_entry,
        args=(start_prompt, audio_file_path, stop_event),
        daemon=True,
    )
    thread.start()

    # 用占位 loop（不会再实际使用），只用于 call_soon_threadsafe 的句柄占位
    # 为了安全，我们在 _thread_entry 创建真实 loop 后会把 handle._loop 替换成真实 loop
    placeholder_loop = asyncio.new_event_loop()
    handle = VoiceDialogHandle(
        thread=thread, loop=placeholder_loop, stop_event=stop_event
    )
    return handle


# 为了把真实 loop 透出给 handle，需要一个中转
def _thread_entry(start_prompt: str, audio_file_path: str, stop_event: asyncio.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 把真实 loop 暴露给最近创建的 handle（简化：用全局注册或更严谨的工厂也可）
    # 这里简单实现：把 loop 存在全局映射里也行。为了最小改动，这里直接运行，不做强耦合替换。
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