import asyncio
import aiohttp
import json
import struct
import gzip
import uuid
import logging
import os
import subprocess
import threading
import queue
import io
import wave
import time
import array
try:
    import sounddevice as sd
except Exception:
    sd = None
from typing import Optional, List, Dict, Any, Tuple, AsyncGenerator

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('run.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 常量定义
DEFAULT_SAMPLE_RATE = 16000

class ProtocolVersion:
    V1 = 0b0001

class MessageType:
    CLIENT_FULL_REQUEST = 0b0001
    CLIENT_AUDIO_ONLY_REQUEST = 0b0010
    SERVER_FULL_RESPONSE = 0b1001
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
    GZIP = 0b0001


class Config:
    def __init__(self):
        # 填入控制台获取的app id和access token
        self.auth = {
            "app_key": "4601805855",
            "access_key": "6b4WXX4EfPhh8W2oLF2B-A9h69BP-qyj"
        }

    @property
    def app_key(self) -> str:
        return self.auth["app_key"]

    @property
    def access_key(self) -> str:
        return self.auth["access_key"]

config = Config()

class CommonUtils:
    @staticmethod
    def gzip_compress(data: bytes) -> bytes:
        return gzip.compress(data)

    @staticmethod
    def gzip_decompress(data: bytes) -> bytes:
        return gzip.decompress(data)

    @staticmethod
    def judge_wav(data: bytes) -> bool:
        if len(data) < 44:
            return False
        return data[:4] == b'RIFF' and data[8:12] == b'WAVE'

    @staticmethod
    def convert_wav_with_path(audio_path: str, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
        try:
            cmd = [
                "ffmpeg", "-v", "quiet", "-y", "-i", audio_path,
                "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(sample_rate),
                "-f", "wav", "-"
            ]
            result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # 尝试删除原始文件
            try:
                os.remove(audio_path)
            except OSError as e:
                logger.warning(f"Failed to remove original file: {e}")
                
            return result.stdout
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg conversion failed: {e.stderr.decode()}")
            raise RuntimeError(f"Audio conversion failed: {e.stderr.decode()}")

    @staticmethod
    def read_wav_info(data: bytes) -> Tuple[int, int, int, int, bytes]:
        if len(data) < 44:
            raise ValueError("Invalid WAV file: too short")
            
        # 解析WAV头
        chunk_id = data[:4]
        if chunk_id != b'RIFF':
            raise ValueError("Invalid WAV file: not RIFF format")
            
        format_ = data[8:12]
        if format_ != b'WAVE':
            raise ValueError("Invalid WAV file: not WAVE format")
            
        # 解析fmt子块
        audio_format = struct.unpack('<H', data[20:22])[0]
        num_channels = struct.unpack('<H', data[22:24])[0]
        sample_rate = struct.unpack('<I', data[24:28])[0]
        bits_per_sample = struct.unpack('<H', data[34:36])[0]
        
        # 查找data子块
        pos = 36
        while pos < len(data) - 8:
            subchunk_id = data[pos:pos+4]
            subchunk_size = struct.unpack('<I', data[pos+4:pos+8])[0]
            if subchunk_id == b'data':
                wave_data = data[pos+8:pos+8+subchunk_size]
                return (
                    num_channels,
                    bits_per_sample // 8,
                    sample_rate,
                    subchunk_size // (num_channels * (bits_per_sample // 8)),
                    wave_data
                )
            pos += 8 + subchunk_size
            
        raise ValueError("Invalid WAV file: no data subchunk found")

class AsrRequestHeader:
    def __init__(self):
        self.message_type = MessageType.CLIENT_FULL_REQUEST
        self.message_type_specific_flags = MessageTypeSpecificFlags.POS_SEQUENCE
        self.serialization_type = SerializationType.JSON
        self.compression_type = CompressionType.GZIP
        self.reserved_data = bytes([0x00])

    def with_message_type(self, message_type: int) -> 'AsrRequestHeader':
        self.message_type = message_type
        return self

    def with_message_type_specific_flags(self, flags: int) -> 'AsrRequestHeader':
        self.message_type_specific_flags = flags
        return self

    def with_serialization_type(self, serialization_type: int) -> 'AsrRequestHeader':
        self.serialization_type = serialization_type
        return self

    def with_compression_type(self, compression_type: int) -> 'AsrRequestHeader':
        self.compression_type = compression_type
        return self

    def with_reserved_data(self, reserved_data: bytes) -> 'AsrRequestHeader':
        self.reserved_data = reserved_data
        return self

    def to_bytes(self) -> bytes:
        header = bytearray()
        header.append((ProtocolVersion.V1 << 4) | 1)
        header.append((self.message_type << 4) | self.message_type_specific_flags)
        header.append((self.serialization_type << 4) | self.compression_type)
        header.extend(self.reserved_data)
        return bytes(header)

    @staticmethod
    def default_header() -> 'AsrRequestHeader':
        return AsrRequestHeader()

class RequestBuilder:
    @staticmethod
    def new_auth_headers() -> Dict[str, str]:
        reqid = str(uuid.uuid4())
        return {
            "X-Api-Resource-Id": "volc.bigasr.sauc.duration",
            "X-Api-Request-Id": reqid,
            "X-Api-Access-Key": config.access_key,
            "X-Api-App-Key": config.app_key
        }

    @staticmethod
    def new_full_client_request(seq: int) -> bytes:  # 添加seq参数
        header = AsrRequestHeader.default_header() \
            .with_message_type_specific_flags(MessageTypeSpecificFlags.POS_SEQUENCE)
        
        payload = {
            "user": {
                "uid": "demo_uid"
            },
            "audio": {
                "format": "wav",
                "codec": "raw",
                "rate": 16000,
                "bits": 16,
                "channel": 1
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": False
            }
        }
        
        payload_bytes = json.dumps(payload).encode('utf-8')
        compressed_payload = CommonUtils.gzip_compress(payload_bytes)
        payload_size = len(compressed_payload)
        
        request = bytearray()
        request.extend(header.to_bytes())
        request.extend(struct.pack('>i', seq))  # 使用传入的seq
        request.extend(struct.pack('>I', payload_size))
        request.extend(compressed_payload)
        
        return bytes(request)

    @staticmethod
    def new_audio_only_request(seq: int, segment: bytes, is_last: bool = False) -> bytes:
        header = AsrRequestHeader.default_header()
        if is_last:  # 最后一个包特殊处理
            header.with_message_type_specific_flags(MessageTypeSpecificFlags.NEG_WITH_SEQUENCE)
            seq = -seq  # 设为负值
        else:
            header.with_message_type_specific_flags(MessageTypeSpecificFlags.POS_SEQUENCE)
        header.with_message_type(MessageType.CLIENT_AUDIO_ONLY_REQUEST)
        
        request = bytearray()
        request.extend(header.to_bytes())
        request.extend(struct.pack('>i', seq))
        
        compressed_segment = CommonUtils.gzip_compress(segment)
        request.extend(struct.pack('>I', len(compressed_segment)))
        request.extend(compressed_segment)
        
        return bytes(request)

class AsrResponse:
    def __init__(self):
        self.code = 0
        self.event = 0
        self.is_last_package = False
        self.payload_sequence = 0
        self.payload_size = 0
        self.payload_msg = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "event": self.event,
            "is_last_package": self.is_last_package,
            "payload_sequence": self.payload_sequence,
            "payload_size": self.payload_size,
            "payload_msg": self.payload_msg
        }

class ResponseParser:
    @staticmethod
    def parse_response(msg: bytes) -> AsrResponse:
        response = AsrResponse()
        
        header_size = msg[0] & 0x0f
        message_type = msg[1] >> 4
        message_type_specific_flags = msg[1] & 0x0f
        serialization_method = msg[2] >> 4
        message_compression = msg[2] & 0x0f
        
        payload = msg[header_size*4:]
        
        # 解析message_type_specific_flags
        if message_type_specific_flags & 0x01:
            response.payload_sequence = struct.unpack('>i', payload[:4])[0]
            payload = payload[4:]
        if message_type_specific_flags & 0x02:
            response.is_last_package = True
        if message_type_specific_flags & 0x04:
            response.event = struct.unpack('>i', payload[:4])[0]
            payload = payload[4:]
            
        # 解析message_type
        if message_type == MessageType.SERVER_FULL_RESPONSE:
            response.payload_size = struct.unpack('>I', payload[:4])[0]
            payload = payload[4:]
        elif message_type == MessageType.SERVER_ERROR_RESPONSE:
            response.code = struct.unpack('>i', payload[:4])[0]
            response.payload_size = struct.unpack('>I', payload[4:8])[0]
            payload = payload[8:]
            
        if not payload:
            return response
            
        # 解压缩
        if message_compression == CompressionType.GZIP:
            try:
                payload = CommonUtils.gzip_decompress(payload)
            except Exception as e:
                logger.error(f"Failed to decompress payload: {e}")
                return response
                
        # 解析payload
        try:
            if serialization_method == SerializationType.JSON:
                response.payload_msg = json.loads(payload.decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to parse payload: {e}")
            
        return response

class AsrWsClient:
    def __init__(self, url: str, segment_duration: int = 200, vad_timeout_ms: int = 1500, vad_threshold: int = 500):
        self.seq = 1
        self.url = url
        self.segment_duration = segment_duration
        self.vad_timeout_ms = vad_timeout_ms  # ms, 0 disables VAD auto-end
        self.vad_threshold = vad_threshold
        self.conn = None
        self.session = None  # 添加session引用

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        if self.conn and not self.conn.closed:
            await self.conn.close()
        if self.session and not self.session.closed:
            await self.session.close()
        
    async def read_audio_data(self, file_path: str) -> bytes:
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
                
            if not CommonUtils.judge_wav(content):
                logger.info("Converting audio to WAV format...")
                content = CommonUtils.convert_wav_with_path(file_path, DEFAULT_SAMPLE_RATE)
                
            return content
        except Exception as e:
            logger.error(f"Failed to read audio data: {e}")
            raise
            
    def get_segment_size(self, content: bytes) -> int:
        try:
            channel_num, samp_width, frame_rate, _, _ = CommonUtils.read_wav_info(content)[:5]
            size_per_sec = channel_num * samp_width * frame_rate
            segment_size = size_per_sec * self.segment_duration // 1000
            return segment_size
        except Exception as e:
            logger.error(f"Failed to calculate segment size: {e}")
            raise
            
    async def create_connection(self) -> None:
        headers = RequestBuilder.new_auth_headers()
        try:
            self.conn = await self.session.ws_connect(  # 使用self.session
                self.url,
                headers=headers
            )
            logger.info(f"Connected to {self.url}")
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            raise
            
    async def send_full_client_request(self) -> None:
        request = RequestBuilder.new_full_client_request(self.seq)
        self.seq += 1  # 发送后递增
        try:
            await self.conn.send_bytes(request)
            logger.info(f"Sent full client request with seq: {self.seq-1}")
            
            msg = await self.conn.receive()
            if msg.type == aiohttp.WSMsgType.BINARY:
                response = ResponseParser.parse_response(msg.data)
                logger.info(f"Received response: {response.to_dict()}")
            else:
                logger.error(f"Unexpected message type: {msg.type}")
        except Exception as e:
            logger.error(f"Failed to send full client request: {e}")
            raise
            
    async def send_messages(self, segment_size: int, content: bytes) -> AsyncGenerator[None, None]:
        audio_segments = self.split_audio(content, segment_size)
        total_segments = len(audio_segments)
        
        for i, segment in enumerate(audio_segments):
            is_last = (i == total_segments - 1)
            request = RequestBuilder.new_audio_only_request(
                self.seq, 
                segment,
                is_last=is_last
            )
            await self.conn.send_bytes(request)
            logger.info(f"Sent audio segment with seq: {self.seq} (last: {is_last})")
            
            if not is_last:
                self.seq += 1
                
            await asyncio.sleep(self.segment_duration / 1000) # 逐个发送，间隔时间模拟实时流
            # 让出控制权，允许接受消息
            yield
            
    async def recv_messages(self) -> AsyncGenerator[AsrResponse, None]:
        try:
            async for msg in self.conn:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    response = ResponseParser.parse_response(msg.data)
                    yield response
                    
                    if response.is_last_package or response.code != 0:
                        break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {msg.data}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    logger.info("WebSocket connection closed")
                    break
        except Exception as e:
            logger.error(f"Error receiving messages: {e}")
            raise
            
    async def start_audio_stream(self, segment_size: int, content: bytes) -> AsyncGenerator[AsrResponse, None]:
        async def sender():
            async for _ in self.send_messages(segment_size, content):
                pass
                
        # 启动发送和接收任务
        sender_task = asyncio.create_task(sender())
        
        try:
            async for response in self.recv_messages():
                yield response
        finally:
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
                
    @staticmethod
    def split_audio(data: bytes, segment_size: int) -> List[bytes]:
        if segment_size <= 0:
            return []
            
        segments = []
        for i in range(0, len(data), segment_size):
            end = i + segment_size
            if end > len(data):
                end = len(data)
            segments.append(data[i:end])
        return segments

    async def execute_mic(self) -> AsyncGenerator[AsrResponse, None]:
        if not self.url:
            raise ValueError("URL is empty")

        if sd is None:
            raise RuntimeError("sounddevice 模块未安装或不可用")

        self.seq = 1

        # 每段包含的帧数
        frames_per_segment = int(DEFAULT_SAMPLE_RATE * self.segment_duration / 1000)
        bytes_per_sample = 2  # pcm_s16le
        segment_size = frames_per_segment * bytes_per_sample * 1

        q: "queue.Queue[bytes]" = queue.Queue()
        stop_event = threading.Event()

        # 标记首包需要包含 WAV 头
        is_first = [True]

        def mic_capture():
            try:
                with sd.RawInputStream(samplerate=DEFAULT_SAMPLE_RATE, channels=1, dtype='int16', blocksize=frames_per_segment) as stream:
                    while not stop_event.is_set():
                        data, overflowed = stream.read(frames_per_segment)
                        if isinstance(data, bytes):
                            q.put(data)
                        else:
                            try:
                                q.put(data.tobytes())
                            except Exception:
                                q.put(bytes(data))
            except Exception as e:
                logger.error(f"Microphone capture error: {e}")
                stop_event.set()

        capture_thread = threading.Thread(target=mic_capture, daemon=True)
        capture_thread.start()

        try:
            await self.create_connection()
            await self.send_full_client_request()

            async def sender():
                loop = asyncio.get_event_loop()
                last_voice_time = time.time()
                vad_timeout_sec = self.vad_timeout_ms / 1000.0 if self.vad_timeout_ms and self.vad_timeout_ms > 0 else None
                while not stop_event.is_set():
                    try:
                        segment = await loop.run_in_executor(None, q.get)
                    except Exception:
                        break
                    if segment is None:
                        continue

                    # 简单 VAD：基于段最大振幅判断是否为语音
                    try:
                        arr = array.array('h')
                        arr.frombytes(segment)
                        max_amp = max((abs(x) for x in arr), default=0)
                    except Exception:
                        max_amp = 0

                    now = time.time()
                    if max_amp > self.vad_threshold:
                        last_voice_time = now

                    # 计数并根据阈值或周期性打印段振幅，帮助调参
                    try:
                        seg_count += 1
                    except NameError:
                        seg_count = 0
                    if max_amp >= self.vad_threshold or (seg_count % 10) == 0:
                        logger.info(f"Segment seq={self.seq} max_amp={max_amp} threshold={self.vad_threshold}")

                    # 如果设定了 vad 超时并且超过静音时长，则发送一个结束包并退出发送循环
                    if vad_timeout_sec is not None and (now - last_voice_time) > vad_timeout_sec:
                        logger.info(f"VAD timeout reached ({self.vad_timeout_ms} ms), sending last packet.")
                        try:
                            last_req = RequestBuilder.new_audio_only_request(self.seq, b'', is_last=True)
                            await self.conn.send_bytes(last_req)
                        except Exception as e:
                            logger.warning(f"Failed to send last packet on VAD timeout: {e}")
                        stop_event.set()
                        break

                    # 如果是第一包，使用 wave 模块生成带 WAV 头的 bytes
                    if is_first[0]:
                        try:
                            buf = io.BytesIO()
                            with wave.open(buf, 'wb') as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)  # int16 -> 2 bytes
                                wf.setframerate(DEFAULT_SAMPLE_RATE)
                                wf.writeframes(segment)
                            packet = buf.getvalue()
                            packet_was_wav = True
                        except Exception as e:
                            logger.error(f"Failed to build WAV header for first packet: {e}")
                            packet = segment
                            packet_was_wav = False
                        is_first[0] = False
                    else:
                        packet = segment
                        packet_was_wav = False

                    try:
                        request = RequestBuilder.new_audio_only_request(self.seq, packet, is_last=False)
                        await self.conn.send_bytes(request)
                        logger.info(f"Sent mic audio segment seq: {self.seq} (wav_header: {packet_was_wav})")
                        self.seq += 1
                    except Exception as e:
                        logger.error(f"Failed to send mic audio segment: {e}")
                        stop_event.set()
                        break
                    await asyncio.sleep(self.segment_duration / 1000)

                # 发送结束包
                try:
                    last_req = RequestBuilder.new_audio_only_request(self.seq, b'', is_last=True)
                    await self.conn.send_bytes(last_req)
                except Exception as e:
                    logger.warning(f"Failed to send last packet: {e}")

            sender_task = asyncio.create_task(sender())

            try:
                async for response in self.recv_messages():
                    yield response
                    if response.is_last_package or response.code != 0:
                        stop_event.set()
                        break
            finally:
                stop_event.set()
                sender_task.cancel()
                try:
                    await sender_task
                except asyncio.CancelledError:
                    pass

        finally:
            stop_event.set()
            try:
                capture_thread.join(timeout=1)
            except Exception:
                pass
            if self.conn:
                await self.conn.close()
        
    async def execute(self, file_path: str) -> AsyncGenerator[AsrResponse, None]:
        if not file_path:
            raise ValueError("File path is empty")
            
        if not self.url:
            raise ValueError("URL is empty")
            
        self.seq = 1
        
        try:
            # 1. 读取音频文件
            content = await self.read_audio_data(file_path)
            
            # 2. 计算分段大小
            segment_size = self.get_segment_size(content)
            
            # 3. 创建WebSocket连接
            await self.create_connection()
            
            # 4. 发送完整客户端请求
            await self.send_full_client_request()
            
            # 5. 启动音频流处理
            async for response in self.start_audio_stream(segment_size, content):
                yield response
                
        except Exception as e:
            logger.error(f"Error in ASR execution: {e}")
            raise
        finally:
            if self.conn:
                await self.conn.close()

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="ASR WebSocket Client")
    parser.add_argument("--file", type=str, required=False, help="Audio file path")
    parser.add_argument("--mic", action="store_true", help="Use microphone for real-time recognition")

    #wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
    #wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
    #wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream
    parser.add_argument("--url", type=str, default="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream", 
                       help="WebSocket URL")
    parser.add_argument("--seg-duration", type=int, default=200, 
                       help="Audio duration(ms) per packet, default:200")
    parser.add_argument("--vad-timeout", type=int, default=1500,
                       help="VAD silence timeout in ms (0 to disable). When exceeded, client sends final is_last packet (default 1500ms)")
    parser.add_argument("--vad-threshold", type=int, default=500,
                       help="VAD amplitude threshold (max abs sample) to consider frame as speech (default 500)")
    
    args = parser.parse_args()
    
    async with AsrWsClient(args.url, args.seg_duration, args.vad_timeout, args.vad_threshold) as client:  # 使用async with
        try:
            if args.mic:
                if sd is None:
                    logger.error("sounddevice 模块不可用，请先安装并确保 PortAudio 可用。")
                    return
                async for response in client.execute_mic():
                    logger.info(f"Received response: {json.dumps(response.to_dict(), indent=2, ensure_ascii=False)}")
            else:
                if not args.file:
                    logger.error("未提供音频文件路径，请使用 --file 或者启用 --mic 使用麦克风。")
                    return
                async for response in client.execute(args.file):
                    logger.info(f"Received response: {json.dumps(response.to_dict(), indent=2, ensure_ascii=False)}")
        except Exception as e:
            logger.error(f"ASR processing failed: {e}")

    
    
    

if __name__ == "__main__":
    asyncio.run(main())

    # 用法：
    # python3 sauc_websocket_demo.py --file /Users/bytedance/code/python/eng_ddc_itn.wav