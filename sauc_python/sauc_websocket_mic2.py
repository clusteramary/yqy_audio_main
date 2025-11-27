# sauc_python/sauc_websocket_mic2.py
import asyncio
import audioop
import gzip
import json
import logging
import struct
import subprocess
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import aiohttp
import pyaudio

# ================== 全局配置 ==================
DEFAULT_SAMPLE_RATE = 16000
SAMPLE_WIDTH_BYTES = 2  # 16bit = 2 bytes
CHANNELS = 2  # 单声道
OUTPUT_FILE = "output.txt"  # 识别结果输出文件

# 日志
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
    HAS_EVENT = 0b0100  # 第 3 bit 标记是否有 event


class SerializationType:
    NO_SERIALIZATION = 0b0000
    JSON = 0b0001


class CompressionType:
    NO_COMPRESSION = 0b0000
    GZIP = 0b0001


# ================== 鉴权配置 ==================
class Config:
    # def __init__(self):
    #     # 这里用你之前的 app_key / access_key
    #     self.auth = {
    #         "app_key": "4601805855",
    #         "access_key": "6b4WXX4EfPhh8W2oLF2B-A9h69BP-qyj",
    #     }
    def __init__(self):
        # 这里用你之前的 app_key / access_key
        self.auth = {
            "app_key": "7381194560",
            "access_key": "PmMJqNvQDStP4xpTi4pnuO83F793BplS",
        }

    @property
    def app_key(self) -> str:
        return self.auth["app_key"]

    @property
    def access_key(self) -> str:
        return self.auth["access_key"]


config = Config()


# ================== 工具函数 ==================
class CommonUtils:
    @staticmethod
    def gzip_compress(data: bytes) -> bytes:
        return gzip.compress(data)

    @staticmethod
    def gzip_decompress(data: bytes) -> bytes:
        return gzip.decompress(data)

    @staticmethod
    def convert_to_pcm_with_path(
        audio_path: str, sample_rate: int = DEFAULT_SAMPLE_RATE
    ) -> bytes:
        """
        使用 ffmpeg 将任意格式音频转为 16k,16bit,单声道 的裸 PCM (s16le)。
        """
        cmd = [
            "ffmpeg",
            "-v",
            "quiet",
            "-y",
            "-i",
            audio_path,
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-",
        ]
        try:
            result = subprocess.run(
                cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg 转码失败: {e.stderr.decode(errors='ignore')}")
            raise RuntimeError(f"音频转码失败: {e.stderr.decode(errors='ignore')}")


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
    def new_auth_headers() -> Dict[str, str]:
        reqid = str(uuid.uuid4())
        return {
            "X-Api-Resource-Id": "volc.bigasr.sauc.duration",
            "X-Api-Request-Id": reqid,
            "X-Api-Access-Key": config.access_key,
            "X-Api-App-Key": config.app_key,
        }

    @staticmethod
    def new_full_client_request(seq: int) -> bytes:
        header = (
            AsrRequestHeader.default_header()
            .with_message_type(MessageType.CLIENT_FULL_REQUEST)
            .with_message_type_specific_flags(MessageTypeSpecificFlags.POS_SEQUENCE)
        )

        # 使用 PCM 裸数据配置，避免 WAV/PCM 不匹配
        payload = {
            "user": {"uid": "demo_uid"},
            "audio": {
                "format": "pcm",  # ❗ 关键：PCM
                "sample_rate": DEFAULT_SAMPLE_RATE,
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
        return buf


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
    def __init__(self, url: str, segment_duration_ms: int = 200):
        self.url = url
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
        headers = RequestBuilder.new_auth_headers()
        assert self.session is not None
        try:
            self.conn = await self.session.ws_connect(self.url, headers=headers)
            logger.info(f"WebSocket 已连接: {self.url}")
        except Exception as e:
            logger.error(f"WebSocket 连接失败: {e}")
            raise

    async def send_full_client_request(self) -> None:
        if self.conn is None:
            raise RuntimeError("WebSocket 尚未连接")
        req_bytes = RequestBuilder.new_full_client_request(self.seq)
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

    def get_segment_size_bytes(self) -> int:
        bytes_per_sec = DEFAULT_SAMPLE_RATE * SAMPLE_WIDTH_BYTES * CHANNELS
        segment_size = bytes_per_sec * self.segment_duration_ms // 1000
        return max(segment_size, 1)

    @staticmethod
    def split_audio(data: bytes, segment_size: int) -> List[bytes]:
        if segment_size <= 0:
            return []
        return [data[i : i + segment_size] for i in range(0, len(data), segment_size)]

    async def send_audio_segments(self, content: bytes) -> None:
        """
        把一整段 PCM 按 segment_size 切块发出去。
        不再 sleep，直接快速发送。
        """
        if self.conn is None:
            raise RuntimeError("WebSocket 尚未连接")

        segment_size = self.get_segment_size_bytes()
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

    # ---------- 执行：PCM / 文件 ----------
    async def execute_with_pcm(
        self, pcm_data: bytes
    ) -> AsyncGenerator[AsrResponse, None]:
        if not pcm_data:
            raise ValueError("PCM 数据为空")
        if not self.url:
            raise ValueError("URL 为空")

        self.seq = 1
        await self.create_connection()
        await self.send_full_client_request()
        await self.send_audio_segments(pcm_data)

        async for resp in self.recv_messages():
            yield resp

        if self.conn:
            await self.conn.close()

    def read_pcm_from_file(self, file_path: str) -> bytes:
        return CommonUtils.convert_to_pcm_with_path(file_path, DEFAULT_SAMPLE_RATE)

    async def execute_file(self, file_path: str) -> AsyncGenerator[AsrResponse, None]:
        pcm = self.read_pcm_from_file(file_path)
        async for resp in self.execute_with_pcm(pcm):
            yield resp


# ================== 麦克风 + VAD（原来的版本，保留） ==================
def record_until_silence(
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    chunk_ms: int = 100,
    silence_threshold: int = 500,
    max_silence_ms: int = 500,  # 默认 0.5s
    max_record_ms: int = 15000,
    device_index: Optional[int] = None,
) -> bytes:
    """
    简单 VAD：检测到说话后，如果持续静音超过 max_silence_ms，就结束录音。
    返回裸 PCM（s16le）数据。
    """
    pa = pyaudio.PyAudio()
    frames: List[bytes] = []

    chunk_size = int(sample_rate * SAMPLE_WIDTH_BYTES * CHANNELS * chunk_ms / 1000)

    open_kwargs = dict(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=sample_rate,
        input=True,
        frames_per_buffer=chunk_size,
    )
    if device_index is not None:
        open_kwargs["input_device_index"] = device_index

    stream = pa.open(**open_kwargs)

    logger.info(
        "开始录音（device_index=%s）：请说话，保持约 %.1f 秒静音后自动结束（Ctrl+C 结束程序）...",
        device_index,
        max_silence_ms / 1000.0,
    )

    speaking = False
    silence_acc_ms = 0
    total_ms = 0

    try:
        while True:
            data = stream.read(chunk_size, exception_on_overflow=False)
            total_ms += chunk_ms

            rms = audioop.rms(data, 2)  # 16bit -> width=2
            if rms > silence_threshold:
                frames.append(data)
                if not speaking:
                    speaking = True
                    logger.info("检测到语音，开始记录...")
                silence_acc_ms = 0
            else:
                if speaking:
                    frames.append(data)
                    silence_acc_ms += chunk_ms
                    if silence_acc_ms >= max_silence_ms:
                        logger.info(f"VAD 静音超时 {max_silence_ms} ms，结束录音。")
                        break

            if total_ms >= max_record_ms:
                logger.info("达到最大录音时长，强制结束录音。")
                break
    except KeyboardInterrupt:
        logger.info("录音被用户中断。")
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    if not frames:
        logger.info("未检测到有效语音。")
        return b""

    pcm = b"".join(frames)
    length_sec = len(pcm) / (sample_rate * SAMPLE_WIDTH_BYTES * CHANNELS)
    logger.info("录音完成，长度约 %.2f 秒。", length_sec)
    return pcm


# ================== 队列版 VAD：从共享 16k PCM 帧队列中录一段话 ==================
async def record_from_queue_until_silence(
    frame_queue,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    frame_ms: int = 20,
    silence_threshold: int = 500,
    max_silence_ms: int = 500,
    max_record_ms: int = 15000,
) -> bytes:
    """
    从一个 asyncio.Queue 中消费 16k/16bit/单声道 PCM 帧（每帧 frame_ms 毫秒），
    做简单 VAD：检测到语音后，当静音持续超过 max_silence_ms 即结束录音。

    注意：这里完全不再使用 PyAudio，只依赖外部往 frame_queue 不断推帧。
    """
    frames: List[bytes] = []
    speaking = False
    silence_acc_ms = 0
    total_ms = 0

    logger.info(
        "[SAUC-QUEUE] 开始从共享队列监听语音 (frame=%d ms, max_silence=%d ms, max_record=%d ms)...",
        frame_ms,
        max_silence_ms,
        max_record_ms,
    )

    try:
        while True:
            # 阻塞等待下一帧（主采集线程会持续 push）
            frame = await frame_queue.get()
            if not frame:
                continue

            total_ms += frame_ms
            rms = audioop.rms(frame, SAMPLE_WIDTH_BYTES)

            if rms > silence_threshold:
                frames.append(frame)
                if not speaking:
                    speaking = True
                    logger.info("[SAUC-QUEUE] 检测到语音，开始记录...")
                silence_acc_ms = 0
            else:
                if speaking:
                    frames.append(frame)
                    silence_acc_ms += frame_ms
                    if silence_acc_ms >= max_silence_ms:
                        logger.info(
                            "[SAUC-QUEUE] 静音超时 %d ms，结束录音。", max_silence_ms
                        )
                        break

            if total_ms >= max_record_ms:
                logger.info(
                    "[SAUC-QUEUE] 达到最大录音时长 %d ms，结束录音。", max_record_ms
                )
                break

    except asyncio.CancelledError:
        logger.info("[SAUC-QUEUE] 录音任务被取消。")
        raise

    if not frames:
        logger.info("[SAUC-QUEUE] 未检测到有效语音。")
        return b""

    pcm = b"".join(frames)
    length_sec = len(pcm) / (sample_rate * SAMPLE_WIDTH_BYTES * CHANNELS)
    logger.info("[SAUC-QUEUE] 录音完成，长度约 %.2f 秒。", length_sec)
    return pcm


# ================== 文本清洗 & 写文件 ==================
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


def write_text_to_file(text: str, path: str = OUTPUT_FILE) -> None:
    cleaned = clean_text(text)
    with open(path, "w", encoding="utf-8") as f:
        if cleaned:
            f.write(cleaned + "\n")
    logger.info("已写入 %s：%s", path, cleaned)


# ================== 提供给 audio_manager 的一次性识别 API（原：麦克版） ==================
async def recognize_once_from_mic(
    url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
    seg_duration: int = 200,
    vad_silence_ms: int = 500,
    vad_threshold: int = 500,
    output_file: str = OUTPUT_FILE,
    device_index: Optional[int] = None,
) -> str:
    """
    一次性麦克风识别：
      - 清空 output_file
      - 录一段话（VAD 静音 0.5s 结束）
      - 调 SAUC 识别
      - 把最终文本写入 output_file
      - 返回识别文本
    """
    # 先清空输出文件
    try:
        open(output_file, "w", encoding="utf-8").close()
    except Exception:
        pass

    logger.info(
        "[SAUC] 准备开始一次麦克风识别（device_index=%s），请对着麦克风说话...",
        device_index,
    )

    loop = asyncio.get_running_loop()
    # 在线程池里执行阻塞的录音，避免把 event loop 卡死
    pcm = await loop.run_in_executor(
        None,
        lambda: record_until_silence(
            sample_rate=DEFAULT_SAMPLE_RATE,
            chunk_ms=100,
            silence_threshold=vad_threshold,
            max_silence_ms=vad_silence_ms,
            max_record_ms=15000,
            device_index=device_index,
        ),
    )

    if not pcm:
        logger.info("[SAUC] 未录到有效语音，本次识别结束。")
        return ""

    final_text = ""

    async with AsrWsClient(url, seg_duration) as client:
        async for resp in client.execute_with_pcm(pcm):
            logger.info(
                "收到响应: %s",
                json.dumps(resp.to_dict(), ensure_ascii=False, indent=2),
            )
            if resp.code != 0:
                logger.error("服务端错误 code=%s", resp.code)
                break
            if resp.is_last_package:
                text = extract_text_from_response(resp)
                final_text = text or ""
                write_text_to_file(final_text, output_file)

    logger.info("[SAUC] 本次识别结果：%r", final_text)
    return final_text


# ================== 新：从共享队列识别一次 ==================
async def recognize_once_from_queue(
    frame_queue,
    url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
    seg_duration: int = 200,
    frame_ms: int = 20,
    vad_silence_ms: int = 500,
    vad_threshold: int = 500,
    max_record_ms: int = 15000,
    output_file: str = OUTPUT_FILE,
) -> str:
    """
    一次性队列识别：
      - 清空 output_file
      - 从 frame_queue 中消费 16k PCM 帧（每帧 frame_ms ms），做 VAD
      - 录到一整句语音后调用 SAUC 识别
      - 把最终文本写入 output_file
      - 返回识别文本

    注意：frame_queue 由外部“统一采集线程”不断 push 帧。
    """
    # 先清空输出文件
    try:
        open(output_file, "w", encoding="utf-8").close()
    except Exception:
        pass

    logger.info("[SAUC] 准备从共享队列捕获一轮语音并识别...")

    pcm = await record_from_queue_until_silence(
        frame_queue=frame_queue,
        sample_rate=DEFAULT_SAMPLE_RATE,
        frame_ms=frame_ms,
        silence_threshold=vad_threshold,
        max_silence_ms=vad_silence_ms,
        max_record_ms=max_record_ms,
    )

    if not pcm:
        logger.info("[SAUC] 队列未录到有效语音，本次识别结束。")
        return ""

    final_text = ""
    async with AsrWsClient(url, seg_duration) as client:
        async for resp in client.execute_with_pcm(pcm):
            logger.info(
                "收到响应: %s",
                json.dumps(resp.to_dict(), ensure_ascii=False, indent=2),
            )
            if resp.code != 0:
                logger.error("服务端错误 code=%s", resp.code)
                break
            if resp.is_last_package:
                text = extract_text_from_response(resp)
                final_text = text or ""
                write_text_to_file(final_text, output_file)

    logger.info("[SAUC] 本次队列识别结果：%r", final_text)
    return final_text


# ================== 命令行入口（保留你的原来能力） ==================
async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="VolcEngine SAUC 大模型 ASR Demo（文件 / 麦克风 + VAD），结果写入 output.txt"
    )
    parser.add_argument(
        "--url",
        type=str,
        default="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream",
        help="WebSocket URL，默认 bigmodel_nostream",
    )
    parser.add_argument(
        "--seg-duration",
        type=int,
        default=200,
        help="每包音频对应的时长 (ms)，默认 200ms",
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--file", type=str, help="要识别的音频文件路径（任意格式，内部转 PCM）"
    )
    group.add_argument(
        "--mic", action="store_true", help="使用麦克风录音一次，静音 0.5s 后结束"
    )

    parser.add_argument(
        "--vad-silence-ms", type=int, default=500, help="VAD 静音超时 (ms)，默认 500"
    )
    parser.add_argument(
        "--vad-threshold", type=int, default=500, help="VAD 能量阈值，默认 500"
    )
    parser.add_argument(
        "--device-index", type=int, default=None, help="录音设备索引，可选"
    )

    args = parser.parse_args()

    # 程序启动时先清空 output.txt
    open(OUTPUT_FILE, "w", encoding="utf-8").close()
    logger.info("已清空 %s", OUTPUT_FILE)

    if args.file:
        # ============ 文件模式：识别一次 ============
        async with AsrWsClient(args.url, args.seg_duration) as client:
            async for resp in client.execute_file(args.file):
                logger.info(
                    "收到响应: %s",
                    json.dumps(resp.to_dict(), ensure_ascii=False, indent=2),
                )
                if resp.code != 0:
                    logger.error("服务端错误 code=%s", resp.code)
                    continue
                if resp.is_last_package:
                    text = extract_text_from_response(resp)
                    write_text_to_file(text, OUTPUT_FILE)
        logger.info("文件识别结束，程序退出。")

    elif args.mic:
        # ============ 麦克风模式：只录一次 ============
        pcm = record_until_silence(
            sample_rate=DEFAULT_SAMPLE_RATE,
            chunk_ms=100,
            silence_threshold=args.vad_threshold,
            max_silence_ms=args.vad_silence_ms,
            max_record_ms=15000,
            device_index=args.device_index,
        )
        if not pcm:
            logger.info("没有有效录音，程序退出。")
            return

        async with AsrWsClient(args.url, args.seg_duration) as client:
            async for resp in client.execute_with_pcm(pcm):
                logger.info(
                    "收到响应: %s",
                    json.dumps(resp.to_dict(), ensure_ascii=False, indent=2),
                )
                if resp.code != 0:
                    logger.error("服务端错误 code=%s", resp.code)
                    break
                if resp.is_last_package:
                    text = extract_text_from_response(resp)
                    write_text_to_file(text, OUTPUT_FILE)

        logger.info("麦克风识别结束，程序退出。")


if __name__ == "__main__":
    asyncio.run(main())
