# dual_mic_asr.py
"""
双麦克风独立识别模块
提供 AsyncASRWorker 类，用于独立管理两个麦克风的语音采集和 ASR 识别
"""
import asyncio
import audioop
import gzip
import json
import logging
import struct
import uuid
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

import aiohttp
import pyaudio

import config

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ================== 协议常量 ==================
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
    HAS_EVENT = 0b0100


class SerializationType:
    NO_SERIALIZATION = 0b0000
    JSON = 0b0001


class CompressionType:
    NO_COMPRESSION = 0b0000
    GZIP = 0b0001


# ================== 工具函数 ==================
class CommonUtils:
    @staticmethod
    def gzip_compress(data: bytes) -> bytes:
        return gzip.compress(data)

    @staticmethod
    def gzip_decompress(data: bytes) -> bytes:
        return gzip.decompress(data)


# ================== 请求头封装 ==================
class AsrRequestHeader:
    def __init__(self):
        self.message_type = MessageType.CLIENT_FULL_REQUEST
        self.message_type_specific_flags = MessageTypeSpecificFlags.POS_SEQUENCE
        self.serialization_type = SerializationType.JSON
        self.compression_type = CompressionType.GZIP
        self.reserved_data = bytes([0x00])

    def with_message_type(self, message_type: int) -> "AsrRequestHeader":
        self.message_type = message_type
        return self

    def with_message_type_specific_flags(self, flags: int) -> "AsrRequestHeader":
        self.message_type_specific_flags = flags
        return self

    def with_serialization_type(self, serialization_type: int) -> "AsrRequestHeader":
        self.serialization_type = serialization_type
        return self

    def with_compression_type(self, compression_type: int) -> "AsrRequestHeader":
        self.compression_type = compression_type
        return self

    def with_reserved_data(self, reserved_data: bytes) -> "AsrRequestHeader":
        self.reserved_data = reserved_data
        return self

    def to_bytes(self) -> bytes:
        """
        4 字节头：
        - 第 1 字节：高 4bit 版本号，低 4bit header 长度(单位 4 字节)，这里固定 1
        - 第 2 字节：高 4bit message_type，低 4bit flags
        - 第 3 字节：高 4bit serialization_type，低 4bit compression_type
        - 第 4 字节：保留
        """
        header = bytearray()
        header.append((ProtocolVersion.V1 << 4) | 0b0001)
        header.append((self.message_type << 4) | self.message_type_specific_flags)
        header.append((self.serialization_type << 4) | self.compression_type)
        header.extend(self.reserved_data)
        return bytes(header)

    @staticmethod
    def default_header() -> "AsrRequestHeader":
        return AsrRequestHeader()


# ================== 请求构造 ==================
class RequestBuilder:
    @staticmethod
    def new_auth_headers(asr_config: Dict[str, str]) -> Dict[str, str]:
        reqid = str(uuid.uuid4())
        return {
            "X-Api-Resource-Id": asr_config["resource_id"],
            "X-Api-Request-Id": reqid,
            "X-Api-Access-Key": asr_config["access_key"],
            "X-Api-App-Key": asr_config["app_key"],
        }

    @staticmethod
    def new_full_client_request(seq: int, audio_config: Dict[str, Any]) -> bytes:
        header = (
            AsrRequestHeader.default_header()
            .with_message_type(MessageType.CLIENT_FULL_REQUEST)
            .with_message_type_specific_flags(MessageTypeSpecificFlags.POS_SEQUENCE)
        )

        payload = {
            "user": {"uid": "demo_uid"},
            "audio": {
                "format": audio_config["format"],
                "sample_rate": audio_config["sample_rate"],
                "bits": audio_config["bits"],
                "channel": audio_config["channels"],
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
        compressed = CommonUtils.gzip_compress(payload_bytes)
        payload_size = len(compressed)

        buf = bytearray()
        buf.extend(header.to_bytes())
        buf.extend(struct.pack(">i", seq))
        buf.extend(struct.pack(">I", payload_size))
        buf.extend(compressed)
        return bytes(buf)

    @staticmethod
    def new_audio_only_request(
        seq: int, segment: bytes, is_last: bool = False
    ) -> bytes:
        header = AsrRequestHeader.default_header().with_message_type(
            MessageType.CLIENT_AUDIO_ONLY_REQUEST
        )

        if is_last:
            header.with_message_type_specific_flags(
                MessageTypeSpecificFlags.NEG_WITH_SEQUENCE
            )
            seq = -abs(seq)
        else:
            header.with_message_type_specific_flags(
                MessageTypeSpecificFlags.POS_SEQUENCE
            )

        buf = bytearray()
        buf.extend(header.to_bytes())
        buf.extend(struct.pack(">i", seq))

        compressed = CommonUtils.gzip_compress(segment)
        buf.extend(struct.pack(">I", len(compressed)))
        buf.extend(compressed)
        return bytes(buf)


# ================== 响应结构与解析 ==================
class AsrResponse:
    def __init__(self) -> None:
        self.code: int = 0
        self.event: int = 0
        self.is_last_package: bool = False
        self.payload_sequence: int = 0
        self.payload_size: int = 0
        self.payload_msg: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "event": self.event,
            "is_last_package": self.is_last_package,
            "payload_sequence": self.payload_sequence,
            "payload_size": self.payload_size,
            "payload_msg": self.payload_msg,
        }


class ResponseParser:
    @staticmethod
    def parse_response(msg: bytes) -> AsrResponse:
        resp = AsrResponse()

        if len(msg) < 4:
            logger.error("响应太短，无法解析")
            return resp

        header_size_words = msg[0] & 0x0F
        message_type = msg[1] >> 4
        flags = msg[1] & 0x0F
        serialization_method = msg[2] >> 4
        compression = msg[2] & 0x0F

        payload = msg[header_size_words * 4 :]

        # flags 解析
        if flags & 0x01:  # 有 sequence
            if len(payload) < 4:
                return resp
            resp.payload_sequence = struct.unpack(">i", payload[:4])[0]
            payload = payload[4:]

        if flags & 0x02:  # 最后一包
            resp.is_last_package = True

        if flags & 0x04:  # 有 event
            if len(payload) < 4:
                return resp
            resp.event = struct.unpack(">i", payload[:4])[0]
            payload = payload[4:]

        # message_type
        if message_type in (MessageType.SERVER_FULL_RESPONSE, MessageType.SERVER_ACK):
            if len(payload) < 4:
                return resp
            resp.payload_size = struct.unpack(">I", payload[:4])[0]
            payload = payload[4:]
        elif message_type == MessageType.SERVER_ERROR_RESPONSE:
            if len(payload) < 8:
                return resp
            resp.code = struct.unpack(">i", payload[:4])[0]
            resp.payload_size = struct.unpack(">I", payload[4:8])[0]
            payload = payload[8:]

        if not payload:
            return resp

        # 解压
        if compression == CompressionType.GZIP:
            try:
                payload = CommonUtils.gzip_decompress(payload)
            except Exception as e:
                logger.error(f"响应解压失败: {e}")
                return resp

        # JSON 解析
        if serialization_method == SerializationType.JSON:
            try:
                resp.payload_msg = json.loads(payload.decode("utf-8"))
            except Exception as e:
                logger.error(f"JSON 解析失败: {e}")

        return resp


# ================== WebSocket 客户端 ==================
class AsrWsClient:
    def __init__(self, url: str, asr_config: Dict[str, str], segment_duration_ms: int = 200):
        self.url = url
        self.asr_config = asr_config
        self.segment_duration_ms = segment_duration_ms
        self.seq = 1
        self.session: Optional[aiohttp.ClientSession] = None
        self.conn: Optional[aiohttp.ClientWebSocketResponse] = None

    async def __aenter__(self) -> "AsrWsClient":
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.conn and not self.conn.closed:
            await self.conn.close()
        if self.session and not self.session.closed:
            await self.session.close()

    async def create_connection(self) -> None:
        headers = RequestBuilder.new_auth_headers(self.asr_config)
        assert self.session is not None
        try:
            self.conn = await self.session.ws_connect(self.url, headers=headers)
            logger.info(f"WebSocket 已连接: {self.url}")
        except Exception as e:
            logger.error(f"WebSocket 连接失败: {e}")
            raise

    async def send_full_client_request(self, audio_config: Dict[str, Any]) -> None:
        if self.conn is None:
            raise RuntimeError("WebSocket 尚未连接")
        req_bytes = RequestBuilder.new_full_client_request(self.seq, audio_config)
        logger.info(f"发送初始化请求 seq={self.seq}")
        await self.conn.send_bytes(req_bytes)
        self.seq += 1

        # 等一次初始化响应
        msg = await self.conn.receive()
        if msg.type == aiohttp.WSMsgType.BINARY:
            resp = ResponseParser.parse_response(msg.data)
            logger.info(
                "初始化响应: %s",
                json.dumps(resp.to_dict(), ensure_ascii=False, indent=2),
            )
        else:
            logger.warning(f"初始化响应类型异常: {msg.type}")

    def get_segment_size_bytes(self, audio_config: Dict[str, Any]) -> int:
        bytes_per_sec = audio_config["sample_rate"] * (audio_config["bits"] // 8) * audio_config["channels"]
        segment_size = bytes_per_sec * self.segment_duration_ms // 1000
        return max(segment_size, 1)

    @staticmethod
    def split_audio(data: bytes, segment_size: int) -> List[bytes]:
        if segment_size <= 0:
            return []
        return [data[i : i + segment_size] for i in range(0, len(data), segment_size)]

    async def send_audio_segments(self, content: bytes, audio_config: Dict[str, Any]) -> None:
        """
        把一整段 PCM 按 segment_size 切块发出去。
        """
        if self.conn is None:
            raise RuntimeError("WebSocket 尚未连接")

        segment_size = self.get_segment_size_bytes(audio_config)
        segments = self.split_audio(content, segment_size)
        total = len(segments)
        if total == 0:
            logger.warning("音频内容为空，跳过发送")
            return

        for idx, seg in enumerate(segments):
            is_last = idx == total - 1
            req = RequestBuilder.new_audio_only_request(self.seq, seg, is_last=is_last)
            await self.conn.send_bytes(req)
            logger.info(f"发送音频分片 seq={self.seq} (last={is_last})")
            if not is_last:
                self.seq += 1

    async def recv_messages(self) -> AsyncGenerator[AsrResponse, None]:
        if self.conn is None:
            raise RuntimeError("WebSocket 尚未连接")
        try:
            async for msg in self.conn:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    resp = ResponseParser.parse_response(msg.data)
                    yield resp
                    if resp.is_last_package or resp.code != 0:
                        break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket 已关闭")
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error("WebSocket 错误: %s", msg.data)
                    break
        except Exception as e:
            logger.error(f"接收消息出错: {e}")
            raise

    async def execute_with_pcm(
        self, pcm_data: bytes, audio_config: Dict[str, Any]
    ) -> AsyncGenerator[AsrResponse, None]:
        if not pcm_data:
            raise ValueError("PCM 数据为空")
        if not self.url:
            raise ValueError("URL 为空")

        self.seq = 1
        await self.create_connection()
        await self.send_full_client_request(audio_config)
        await self.send_audio_segments(pcm_data, audio_config)

        async for resp in self.recv_messages():
            yield resp

        if self.conn:
            await self.conn.close()


# ================== 文本提取 ==================
def extract_text_from_response(resp: AsrResponse) -> str:
    try:
        if not resp.payload_msg:
            return ""
        result = resp.payload_msg.get("result") or {}
        return result.get("text", "") or ""
    except Exception:
        return ""


def clean_text(text: str) -> str:
    """
    简单清洗：去掉不可打印字符和常见乱码替代符号（如 U+FFFD），避免 '��' 之类。
    """
    text = "".join(ch for ch in text if ch.isprintable() or ch in "\n\t ")
    text = text.replace("\ufffd", "")
    return text.strip()


# ================== AsyncASRWorker 类 ==================
class AsyncASRWorker:
    """
    异步 ASR Worker，用于独立管理一个麦克风的语音采集和识别
    """

    def __init__(
        self,
        speaker_label: str,
        device_index: Optional[int],
        on_text_recognized: Callable[[str, str], None],
        asr_config: Optional[Dict[str, str]] = None,
        audio_config: Optional[Dict[str, Any]] = None,
        vad_config: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化 ASR Worker
        
        Args:
            speaker_label: 说话人标签（如 "嘉宾" 或 "辅助记者"）
            device_index: 麦克风设备索引
            on_text_recognized: 回调函数，接收 (text, speaker_label)
            asr_config: ASR 配置（默认使用 config.ASR_CONFIG）
            audio_config: 音频配置（默认使用 config.ASR_AUDIO_CONFIG）
            vad_config: VAD 配置（默认使用 config.ASR_VAD_CONFIG）
        """
        self.speaker_label = speaker_label
        self.device_index = device_index
        self.on_text_recognized = on_text_recognized
        
        # 配置
        self.asr_config = asr_config or config.ASR_CONFIG
        self.audio_config = audio_config or config.ASR_AUDIO_CONFIG
        self.vad_config = vad_config or config.ASR_VAD_CONFIG
        
        # 运行状态
        self.is_running = False
        self.worker_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """启动 ASR Worker"""
        if self.is_running:
            logger.warning(f"[{self.speaker_label}] ASR Worker 已经在运行")
            return

        self.is_running = True
        self.worker_task = asyncio.create_task(self._worker_loop())
        logger.info(f"[{self.speaker_label}] ASR Worker 已启动")

    async def stop(self) -> None:
        """停止 ASR Worker"""
        if not self.is_running:
            return

        self.is_running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        logger.info(f"[{self.speaker_label}] ASR Worker 已停止")

    async def _worker_loop(self) -> None:
        """
        Worker 主循环：
        1. 持续监听麦克风
        2. 使用 VAD 检测语音
        3. 发送到 ASR 服务识别
        4. 通过回调返回识别结果
        """
        try:
            while self.is_running:
                # 录制一段语音（使用 VAD）
                pcm_data = await self._record_until_silence()
                
                if not pcm_data:
                    # 没有检测到有效语音，继续监听
                    await asyncio.sleep(0.1)
                    continue

                # 发送到 ASR 服务识别
                final_text = await self._recognize_pcm(pcm_data)
                
                if final_text:
                    # 通过回调返回识别结果
                    self.on_text_recognized(final_text, self.speaker_label)
                
                # 短暂休息，避免过于频繁
                await asyncio.sleep(0.2)
                
        except asyncio.CancelledError:
            logger.info(f"[{self.speaker_label}] Worker 循环被取消")
            raise
        except Exception as e:
            logger.error(f"[{self.speaker_label}] Worker 循环异常: {e}")

    async def _record_until_silence(self) -> bytes:
        """
        从麦克风录制一段语音，使用 VAD 检测静音
        返回 PCM 数据
        """
        loop = asyncio.get_running_loop()
        
        # 在线程池中执行阻塞的录音操作
        pcm_data = await loop.run_in_executor(
            None,
            self._sync_record_until_silence
        )
        
        return pcm_data

    def _sync_record_until_silence(self) -> bytes:
        """
        同步录音函数（在线程池中执行）
        使用 VAD 检测静音，返回 PCM 数据
        """
        pa = pyaudio.PyAudio()
        frames: List[bytes] = []

        chunk_size = int(
            self.audio_config["sample_rate"]
            * (self.audio_config["bits"] // 8)
            * self.audio_config["channels"]
            * self.vad_config["chunk_ms"]
            / 1000
        )

        open_kwargs = dict(
            format=pyaudio.paInt16,
            channels=self.audio_config["channels"],
            rate=self.audio_config["sample_rate"],
            input=True,
            frames_per_buffer=chunk_size,
        )
        if self.device_index is not None:
            open_kwargs["input_device_index"] = self.device_index

        stream = pa.open(**open_kwargs)

        logger.info(
            f"[{self.speaker_label}] 开始录音（device_index={self.device_index}）..."
        )

        speaking = False
        silence_acc_ms = 0
        total_ms = 0

        try:
            while self.is_running:
                data = stream.read(chunk_size, exception_on_overflow=False)
                total_ms += self.vad_config["chunk_ms"]

                rms = audioop.rms(data, 2)  # 16bit -> width=2
                if rms > self.vad_config["silence_threshold"]:
                    frames.append(data)
                    if not speaking:
                        speaking = True
                        logger.info(f"[{self.speaker_label}] 检测到语音，开始记录...")
                    silence_acc_ms = 0
                else:
                    if speaking:
                        frames.append(data)
                        silence_acc_ms += self.vad_config["chunk_ms"]
                        if silence_acc_ms >= self.vad_config["max_silence_ms"]:
                            logger.info(
                                f"[{self.speaker_label}] VAD 静音超时 {self.vad_config['max_silence_ms']} ms，结束录音。"
                            )
                            break

                if total_ms >= self.vad_config["max_record_ms"]:
                    logger.info(
                        f"[{self.speaker_label}] 达到最大录音时长，强制结束录音。"
                    )
                    break
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

        if not frames:
            logger.info(f"[{self.speaker_label}] 未检测到有效语音。")
            return b""

        pcm = b"".join(frames)
        length_sec = (
            len(pcm)
            / (
                self.audio_config["sample_rate"]
                * (self.audio_config["bits"] // 8)
                * self.audio_config["channels"]
            )
        )
        logger.info(f"[{self.speaker_label}] 录音完成，长度约 %.2f 秒。", length_sec)
        return pcm

    async def _recognize_pcm(self, pcm_data: bytes) -> str:
        """
        将 PCM 数据发送到 ASR 服务进行识别
        返回识别文本
        """
        final_text = ""

        try:
            async with AsrWsClient(
                self.asr_config["url"], self.asr_config, segment_duration_ms=200
            ) as client:
                async for resp in client.execute_with_pcm(pcm_data, self.audio_config):
                    logger.info(
                        f"[{self.speaker_label}] 收到响应: %s",
                        json.dumps(resp.to_dict(), ensure_ascii=False, indent=2),
                    )
                    if resp.code != 0:
                        logger.error(f"[{self.speaker_label}] 服务端错误 code={resp.code}")
                        break
                    if resp.is_last_package:
                        text = extract_text_from_response(resp)
                        final_text = clean_text(text or "")
                        logger.info(f"[{self.speaker_label}] 本次识别结果：{final_text!r}")

        except Exception as e:
            logger.error(f"[{self.speaker_label}] ASR 识别异常: {e}")

        return final_text


# ================== 工具函数 ==================
def list_audio_devices() -> None:
    """
    列出所有可用的音频设备
    用于查找麦克风设备索引
    """
    pa = pyaudio.PyAudio()
    print("=" * 60)
    print("可用音频设备列表：")
    print("=" * 60)
    
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            print(f"设备索引: {i}")
            print(f"  名称: {info['name']}")
            print(f"  最大输入通道: {info['maxInputChannels']}")
            print(f"  采样率: {int(info['defaultSampleRate'])} Hz")
            print("-" * 60)
    
    pa.terminate()


if __name__ == "__main__":
    # 测试：列出音频设备
    list_audio_devices()
