# dual_mic_asr.py
"""
双麦克风 ASR 模块：支持两个独立麦克风同时进行语音识别。
用于三人深度采访场景：一个嘉宾、一个辅助记者、一个机器人主持人。

核心功能：
1. 两个独立的 PyAudio 输入流，分别监听嘉宾和辅助记者的麦克风
2. 本地 VAD（语音活动检测）过滤静音，避免无效的 ASR 调用
3. WebSocket 连接火山引擎 ASR 服务进行语音转文字
4. 回调机制：识别完成后通过回调函数返回带标签的文本
"""

import asyncio
import audioop
import gzip
import json
import logging
import os
import struct
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import pyaudio

import config

# ================== 修复 Linux 系统 PyAudio 初始化问题 ==================
# 禁用有问题的 ALSA 插件，避免断言失败
os.environ['ALSA_CARD'] = 'Generic'
os.environ['ALSA_PCM_CARD'] = 'Generic'

# ================== 日志配置 ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(name)s] %(message)s",
)
logger = logging.getLogger("DualMicASR")


# ================== 协议常量（复用 sauc_websocket_mic2.py） ==================
class ProtocolVersion:
    V1 = 0b0001


class MessageType:
    CLIENT_FULL_REQUEST = 0b0001
    CLIENT_AUDIO_ONLY_REQUEST = 0b0010
    SERVER_FULL_RESPONSE = 0b1001
    SERVER_ACK = 0b1011
    SERVER_ERROR_RESPONSE = 0b1111


class MessageTypeSpecificFlags:
    NO_SEQUENCE = 0b0000
    POS_SEQUENCE = 0b0001
    NEG_SEQUENCE = 0b0010
    NEG_WITH_SEQUENCE = 0b0011


class SerializationType:
    NO_SERIALIZATION = 0b0000
    JSON = 0b0001


class CompressionType:
    NO_COMPRESSION = 0b0000
    GZIP = 0b0001


# ================== 工具函数 ==================
def gzip_compress(data: bytes) -> bytes:
    return gzip.compress(data)


def gzip_decompress(data: bytes) -> bytes:
    return gzip.decompress(data)


# ================== ASR 请求构造 ==================
class AsrRequestBuilder:
    """ASR WebSocket 请求构造器"""

    def __init__(self, app_key: str, access_key: str):
        self.app_key = app_key
        self.access_key = access_key

    def build_headers(self) -> Dict[str, str]:
        """构造 HTTP Headers"""
        return {
            "X-Api-Resource-Id": "volc.bigasr.sauc.duration",
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Access-Key": self.access_key,
            "X-Api-App-Key": self.app_key,
        }

    def build_init_request(self, seq: int) -> bytes:
        """构造初始化请求"""
        header = self._build_header(
            MessageType.CLIENT_FULL_REQUEST, MessageTypeSpecificFlags.POS_SEQUENCE
        )

        payload = {
            "user": {"uid": "dual_mic_user"},
            "audio": {
                "format": "pcm",
                "sample_rate": config.dual_mic_asr_config["sample_rate"],
                "bits": 16,
                "channel": 1,
                "codec": "raw",
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": False,
            },
        }

        payload_bytes = json.dumps(payload).encode("utf-8")
        compressed = gzip_compress(payload_bytes)

        buf = bytearray()
        buf.extend(header)
        buf.extend(struct.pack(">i", seq))
        buf.extend(struct.pack(">I", len(compressed)))
        buf.extend(compressed)
        return bytes(buf)

    def build_audio_request(self, seq: int, audio_data: bytes, is_last: bool) -> bytes:
        """构造音频数据请求"""
        if is_last:
            flags = MessageTypeSpecificFlags.NEG_WITH_SEQUENCE
            seq = -abs(seq)
        else:
            flags = MessageTypeSpecificFlags.POS_SEQUENCE

        header = self._build_header(MessageType.CLIENT_AUDIO_ONLY_REQUEST, flags)

        compressed = gzip_compress(audio_data)

        buf = bytearray()
        buf.extend(header)
        buf.extend(struct.pack(">i", seq))
        buf.extend(struct.pack(">I", len(compressed)))
        buf.extend(compressed)
        return bytes(buf)

    def _build_header(self, msg_type: int, flags: int) -> bytes:
        """构造 4 字节协议头"""
        header = bytearray()
        header.append((ProtocolVersion.V1 << 4) | 0b0001)
        header.append((msg_type << 4) | flags)
        header.append((SerializationType.JSON << 4) | CompressionType.GZIP)
        header.append(0x00)  # 保留字节
        return bytes(header)


# ================== ASR 响应解析 ==================
class AsrResponse:
    """ASR 响应结构"""

    def __init__(self):
        self.code: int = 0
        self.is_last: bool = False
        self.text: str = ""
        self.payload: Optional[Dict[str, Any]] = None


def parse_asr_response(data: bytes) -> AsrResponse:
    """解析 ASR 响应"""
    resp = AsrResponse()

    if len(data) < 4:
        return resp

    header_size = data[0] & 0x0F
    msg_type = data[1] >> 4
    flags = data[1] & 0x0F
    compression = data[2] & 0x0F

    payload = data[header_size * 4 :]

    # 解析 flags
    if flags & 0x01:  # 有 sequence
        if len(payload) >= 4:
            payload = payload[4:]

    if flags & 0x02:  # 最后一包
        resp.is_last = True

    if flags & 0x04:  # 有 event
        if len(payload) >= 4:
            payload = payload[4:]

    # 解析 payload size
    if msg_type in (MessageType.SERVER_FULL_RESPONSE, MessageType.SERVER_ACK):
        if len(payload) >= 4:
            payload = payload[4:]
    elif msg_type == MessageType.SERVER_ERROR_RESPONSE:
        if len(payload) >= 8:
            resp.code = struct.unpack(">i", payload[:4])[0]
            payload = payload[8:]

    if not payload:
        return resp

    # 解压
    if compression == CompressionType.GZIP:
        try:
            payload = gzip_decompress(payload)
        except Exception:
            return resp

    # JSON 解析
    try:
        resp.payload = json.loads(payload.decode("utf-8"))
        if resp.payload:
            result = resp.payload.get("result", {})
            resp.text = result.get("text", "")
    except Exception:
        pass

    return resp


# ================== 单路麦克风 ASR Worker ==================
class MicASRWorker:
    """
    单路麦克风 ASR 工作器
    
    负责：
    1. 打开指定的麦克风设备
    2. 持续进行 VAD 检测
    3. 检测到语音后进行 ASR 识别
    4. 通过回调返回识别结果
    """

    def __init__(
        self,
        device_index: int,
        speaker_label: str,  # "guest" 或 "assistant"
        on_text_callback: Callable[[str, str], None],  # callback(text, label)
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_ms: int = 100,
        vad_threshold: int = 500,
        vad_silence_ms: int = 600,
        max_record_ms: int = 30000,
    ):
        self.device_index = device_index
        self.speaker_label = speaker_label
        self.on_text_callback = on_text_callback
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_ms = chunk_ms
        self.vad_threshold = vad_threshold
        self.vad_silence_ms = vad_silence_ms
        self.max_record_ms = max_record_ms

        # 计算每帧字节数
        self.bytes_per_sample = 2  # 16bit
        self.chunk_samples = int(sample_rate * chunk_ms / 1000)
        self.chunk_bytes = self.chunk_samples * self.bytes_per_sample * channels

        # PyAudio 实例（每个 Worker 独立）
        self.pa: Optional[pyaudio.PyAudio] = None
        self.stream: Optional[pyaudio.Stream] = None

        # 控制标志
        self.running = False
        self._stop_event = threading.Event()

        # ASR 请求构造器
        self.request_builder = AsrRequestBuilder(
            app_key=config.ws_connect_config["headers"]["X-Api-App-ID"],
            access_key=config.ws_connect_config["headers"]["X-Api-Access-Key"],
        )

        # ASR URL
        self.asr_url = config.dual_mic_asr_config["url"]

        logger.info(
            f"[{self.speaker_label}] MicASRWorker 初始化完成，设备索引: {device_index}"
        )

    def start(self) -> None:
        """启动 Worker（在独立线程中运行）"""
        if self.running:
            logger.warning(f"[{self.speaker_label}] Worker 已在运行")
            return

        self._stop_event.clear()
        self.running = True

        # 启动工作线程
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"[{self.speaker_label}] Worker 已启动")

    def stop(self) -> None:
        """停止 Worker"""
        if not self.running:
            return

        self._stop_event.set()
        self.running = False

        if hasattr(self, "_thread") and self._thread.is_alive():
            self._thread.join(timeout=2.0)

        self._close_stream()
        logger.info(f"[{self.speaker_label}] Worker 已停止")

    def _open_stream(self) -> bool:
        """打开麦克风流"""
        try:
            # 使用 try-except 包裹 PyAudio 初始化，处理可能的断言失败
            try:
                self.pa = pyaudio.PyAudio()
            except Exception as e:
                logger.error(f"[{self.speaker_label}] PyAudio 初始化失败: {e}")
                # 尝试设置环境变量后重试
                os.environ['JACK_NO_AUDIO_RESERVATION'] = '1'
                os.environ['PULSE_LATENCY_MSEC'] = '60'
                try:
                    self.pa = pyaudio.PyAudio()
                except Exception as e2:
                    logger.error(f"[{self.speaker_label}] PyAudio 重试初始化失败: {e2}")
                    return False

            # 验证设备索引是否有效
            if self.device_index >= self.pa.get_device_count():
                logger.error(
                    f"[{self.speaker_label}] 无效的设备索引: {self.device_index} "
                    f"(总设备数: {self.pa.get_device_count()})"
                )
                self.pa.terminate()
                self.pa = None
                return False

            # 获取设备信息并验证
            try:
                dev_info = self.pa.get_device_info_by_index(self.device_index)
                if dev_info['maxInputChannels'] < self.channels:
                    logger.error(
                        f"[{self.speaker_label}] 设备不支持 {self.channels} 个输入通道 "
                        f"(最大: {dev_info['maxInputChannels']})"
                    )
                    self.pa.terminate()
                    self.pa = None
                    return False
                logger.info(
                    f"[{self.speaker_label}] 使用设备: {dev_info['name']} "
                    f"(索引: {self.device_index})"
                )
            except Exception as e:
                logger.error(f"[{self.speaker_label}] 获取设备信息失败: {e}")
                self.pa.terminate()
                self.pa = None
                return False

            # 打开音频流
            self.stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.chunk_samples,
            )
            logger.info(
                f"[{self.speaker_label}] 麦克风流已打开，设备: {self.device_index}"
            )
            return True
        except Exception as e:
            logger.error(f"[{self.speaker_label}] 打开麦克风失败: {e}")
            if self.pa:
                try:
                    self.pa.terminate()
                except Exception:
                    pass
                self.pa = None
            return False

    def _close_stream(self) -> None:
        """关闭麦克风流"""
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if self.pa:
            try:
                self.pa.terminate()
            except Exception:
                pass
            self.pa = None

    def _run_loop(self) -> None:
        """主工作循环（在独立线程中运行）"""
        if not self._open_stream():
            self.running = False
            return

        logger.info(f"[{self.speaker_label}] 开始监听麦克风...")

        while not self._stop_event.is_set():
            try:
                # 进行一次 VAD + ASR
                pcm_data = self._record_with_vad()

                if pcm_data and len(pcm_data) > 0:
                    # 在新的事件循环中运行 ASR
                    text = self._run_asr_sync(pcm_data)
                    if text:
                        logger.info(
                            f"[{self.speaker_label}] 识别结果: {text}"
                        )
                        # 调用回调
                        if self.on_text_callback:
                            self.on_text_callback(text, self.speaker_label)

            except Exception as e:
                logger.error(f"[{self.speaker_label}] 工作循环错误: {e}")
                if self._stop_event.is_set():
                    break

        self._close_stream()

    def _record_with_vad(self) -> bytes:
        """
        VAD 录音：检测到语音后录制，静音超时后返回
        返回 PCM 数据（16k/16bit/单声道）
        """
        frames: List[bytes] = []
        speaking = False
        silence_ms = 0
        total_ms = 0

        while not self._stop_event.is_set():
            try:
                data = self.stream.read(self.chunk_samples, exception_on_overflow=False)
            except Exception as e:
                logger.warning(f"[{self.speaker_label}] 读取音频失败: {e}")
                break

            total_ms += self.chunk_ms

            # 计算 RMS（音量）
            try:
                rms = audioop.rms(data, 2)  # 16bit -> width=2
            except Exception:
                rms = 0

            if rms > self.vad_threshold:
                # 检测到语音
                frames.append(data)
                if not speaking:
                    speaking = True
                    logger.debug(f"[{self.speaker_label}] 检测到语音，开始录制...")
                silence_ms = 0
            else:
                # 静音
                if speaking:
                    frames.append(data)
                    silence_ms += self.chunk_ms
                    if silence_ms >= self.vad_silence_ms:
                        logger.debug(
                            f"[{self.speaker_label}] 静音超时 {self.vad_silence_ms}ms，结束录制"
                        )
                        break

            # 最大录制时长限制
            if total_ms >= self.max_record_ms:
                logger.debug(f"[{self.speaker_label}] 达到最大录制时长，结束")
                break

        if not frames:
            return b""

        return b"".join(frames)

    def _run_asr_sync(self, pcm_data: bytes) -> str:
        """同步运行 ASR（内部创建事件循环）"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._do_asr(pcm_data))
        finally:
            loop.close()

    async def _do_asr(self, pcm_data: bytes) -> str:
        """执行 ASR 识别"""
        if not pcm_data:
            return ""

        final_text = ""
        seq = 1

        try:
            async with aiohttp.ClientSession() as session:
                headers = self.request_builder.build_headers()
                async with session.ws_connect(
                    self.asr_url, headers=headers
                ) as ws:
                    # 发送初始化请求
                    init_req = self.request_builder.build_init_request(seq)
                    await ws.send_bytes(init_req)
                    seq += 1

                    # 等待初始化响应
                    msg = await ws.receive()
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        resp = parse_asr_response(msg.data)
                        logger.debug(f"[{self.speaker_label}] 初始化响应: {resp.payload}")

                    # 分片发送音频
                    segment_size = int(
                        self.sample_rate * self.bytes_per_sample * 0.2
                    )  # 200ms
                    segments = [
                        pcm_data[i : i + segment_size]
                        for i in range(0, len(pcm_data), segment_size)
                    ]

                    for idx, seg in enumerate(segments):
                        is_last = idx == len(segments) - 1
                        audio_req = self.request_builder.build_audio_request(
                            seq, seg, is_last
                        )
                        await ws.send_bytes(audio_req)
                        if not is_last:
                            seq += 1

                    # 接收响应
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            resp = parse_asr_response(msg.data)
                            if resp.code != 0:
                                logger.error(
                                    f"[{self.speaker_label}] ASR 错误: {resp.code}"
                                )
                                break
                            if resp.is_last and resp.text:
                                final_text = resp.text
                                break
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break

        except Exception as e:
            logger.error(f"[{self.speaker_label}] ASR 请求失败: {e}")

        return final_text


# ================== 双麦克风管理器 ==================
class DualMicManager:
    """
    双麦克风管理器
    
    管理两个独立的 MicASRWorker，分别处理嘉宾和辅助记者的麦克风。
    """

    def __init__(
        self,
        guest_device_index: int,
        assistant_device_index: int,
        on_guest_text: Callable[[str], None],
        on_assistant_text: Callable[[str], None],
    ):
        """
        Args:
            guest_device_index: 嘉宾麦克风设备索引
            assistant_device_index: 辅助记者麦克风设备索引
            on_guest_text: 嘉宾语音识别回调 callback(text)
            on_assistant_text: 辅助记者语音识别回调 callback(text)
        """
        self.on_guest_text = on_guest_text
        self.on_assistant_text = on_assistant_text

        asr_cfg = config.dual_mic_asr_config

        # 创建嘉宾麦克风 Worker
        self.guest_worker = MicASRWorker(
            device_index=guest_device_index,
            speaker_label="guest",
            on_text_callback=self._on_text_recognized,
            sample_rate=asr_cfg["sample_rate"],
            channels=asr_cfg["channels"],
            chunk_ms=asr_cfg["chunk_ms"],
            vad_threshold=asr_cfg["vad_threshold"],
            vad_silence_ms=asr_cfg["vad_silence_ms"],
            max_record_ms=asr_cfg["max_record_ms"],
        )

        # 创建辅助记者麦克风 Worker
        self.assistant_worker = MicASRWorker(
            device_index=assistant_device_index,
            speaker_label="assistant",
            on_text_callback=self._on_text_recognized,
            sample_rate=asr_cfg["sample_rate"],
            channels=asr_cfg["channels"],
            chunk_ms=asr_cfg["chunk_ms"],
            vad_threshold=asr_cfg["vad_threshold"],
            vad_silence_ms=asr_cfg["vad_silence_ms"],
            max_record_ms=asr_cfg["max_record_ms"],
        )

        logger.info(
            f"DualMicManager 初始化完成: 嘉宾麦克风={guest_device_index}, "
            f"辅助记者麦克风={assistant_device_index}"
        )

    def _on_text_recognized(self, text: str, label: str) -> None:
        """内部回调：分发到对应的外部回调"""
        if label == "guest":
            if self.on_guest_text:
                self.on_guest_text(text)
        elif label == "assistant":
            if self.on_assistant_text:
                self.on_assistant_text(text)

    def start(self) -> None:
        """启动双麦克风监听"""
        logger.info("启动双麦克风监听...")
        self.guest_worker.start()
        self.assistant_worker.start()

    def stop(self) -> None:
        """停止双麦克风监听"""
        logger.info("停止双麦克风监听...")
        self.guest_worker.stop()
        self.assistant_worker.stop()


# ================== 工具函数：列出可用麦克风 ==================
def list_audio_devices() -> List[Dict[str, Any]]:
    """列出所有可用的音频输入设备"""
    devices = []
    
    try:
        pa = pyaudio.PyAudio()
    except Exception as e:
        logger.error(f"PyAudio 初始化失败: {e}")
        print(f"\n错误：无法初始化 PyAudio。请尝试以下解决方案：")
        print("1. 确保安装了正确的音频驱动")
        print("2. 运行: sudo apt-get install libasound2-dev portaudio19-dev (Ubuntu/Debian)")
        print("3. 检查系统音频服务是否正常运行")
        return devices

    try:
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0:  # 只列出输入设备
                    devices.append(
                        {
                            "index": i,
                            "name": info["name"],
                            "channels": info["maxInputChannels"],
                            "sample_rate": int(info["defaultSampleRate"]),
                        }
                    )
            except Exception as e:
                # 跳过无效的设备
                logger.debug(f"跳过设备 {i}: {e}")
                continue
    finally:
        try:
            pa.terminate()
        except Exception:
            pass

    return devices


def print_audio_devices() -> None:
    """打印所有可用的音频输入设备"""
    devices = list_audio_devices()
    print("\n" + "=" * 60)
    print("可用的麦克风设备：")
    print("=" * 60)
    for dev in devices:
        print(
            f"  索引 {dev['index']:2d}: {dev['name']} "
            f"(通道数: {dev['channels']}, 采样率: {dev['sample_rate']})"
        )
    print("=" * 60)
    print("\n请在 config.py 中设置 GUEST_MIC_INDEX 和 ASSISTANT_MIC_INDEX\n")


# ================== 测试入口 ==================
if __name__ == "__main__":
    print_audio_devices()

    # 简单测试：使用默认麦克风进行一次识别
    print("\n开始测试单麦克风识别...")

    def on_text(text: str, label: str) -> None:
        print(f"\n[{label}] 识别结果: {text}\n")

    # 使用设备索引 0 进行测试
    worker = MicASRWorker(
        device_index=0,
        speaker_label="test",
        on_text_callback=on_text,
    )

    print("按 Ctrl+C 停止...")
    try:
        worker.start()
        # 保持运行
        import time

        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n停止测试...")
        worker.stop()
