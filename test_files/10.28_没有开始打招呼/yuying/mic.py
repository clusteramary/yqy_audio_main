import asyncio
import gzip
import json
import logging
import os
import struct
import subprocess
import uuid
from io import BytesIO
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import aiohttp
import numpy as np
import pyaudio

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
MIC_SAMPLE_RATE = 48000  # 麦克风采样率

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
            "app_key": "5167059820",
            "access_key": "spzdT_8qFFeghO2oiFBMaI0p9RbVR35k"
        }

    @property
    def app_key(self) -> str:
        return self.auth["app_key"]

    @property
    def access_key(self) -> str:
        return self.auth["access_key"]

config = Config()

class AudioUtils:
    @staticmethod
    def resample_audio(audio_data: bytes, original_rate: int, target_rate: int) -> bytes:
        """
        将音频数据从原始采样率重采样到目标采样率
        """
        try:
            # 将字节数据转换为numpy数组
            audio_array = np.frombuffer(audio_data, dtype=np.int16)
            
            # 计算重采样比例
            ratio = target_rate / original_rate
            new_length = int(len(audio_array) * ratio)
            
            # 使用线性插值进行重采样
            original_indices = np.arange(len(audio_array))
            target_indices = np.linspace(0, len(audio_array)-1, new_length)
            resampled_array = np.interp(target_indices, original_indices, audio_array).astype(np.int16)
            
            return resampled_array.tobytes()
        except Exception as e:
            logger.error(f"Audio resampling failed: {e}")
            raise

    @staticmethod
    def create_wav_header(sample_rate: int, audio_data: bytes) -> bytes:
        """
        创建WAV文件头
        """
        num_channels = 1
        bits_per_sample = 16
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        block_align = num_channels * bits_per_sample // 8
        data_size = len(audio_data)
        file_size = data_size + 36
        
        wav_header = bytearray()
        # RIFF header
        wav_header.extend(b'RIFF')
        wav_header.extend(struct.pack('<I', file_size))
        wav_header.extend(b'WAVE')
        # fmt chunk
        wav_header.extend(b'fmt ')
        wav_header.extend(struct.pack('<I', 16))  # PCM chunk size
        wav_header.extend(struct.pack('<H', 1))   # PCM format
        wav_header.extend(struct.pack('<H', num_channels))
        wav_header.extend(struct.pack('<I', sample_rate))
        wav_header.extend(struct.pack('<I', byte_rate))
        wav_header.extend(struct.pack('<H', block_align))
        wav_header.extend(struct.pack('<H', bits_per_sample))
        # data chunk
        wav_header.extend(b'data')
        wav_header.extend(struct.pack('<I', data_size))
        
        return bytes(wav_header) + audio_data

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
    def new_full_client_request(seq: int) -> bytes:
        header = AsrRequestHeader.default_header() \
            .with_message_type_specific_flags(MessageTypeSpecificFlags.POS_SEQUENCE)
        
        payload = {
            "user": {
                "uid": "demo_uid"
            },
            "audio": {
                "format": "wav",
                "codec": "raw",
                "rate": 16000,  # 目标采样率16kHz
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
        request.extend(struct.pack('>i', seq))
        request.extend(struct.pack('>I', payload_size))
        request.extend(compressed_payload)
        
        return bytes(request)

    @staticmethod
    def new_audio_only_request(seq: int, segment: bytes, is_last: bool = False) -> bytes:
        header = AsrRequestHeader.default_header()
        if is_last:
            header.with_message_type_specific_flags(MessageTypeSpecificFlags.NEG_WITH_SEQUENCE)
            seq = -seq
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
        
        if message_type_specific_flags & 0x01:
            response.payload_sequence = struct.unpack('>i', payload[:4])[0]
            payload = payload[4:]
        if message_type_specific_flags & 0x02:
            response.is_last_package = True
        if message_type_specific_flags & 0x04:
            response.event = struct.unpack('>i', payload[:4])[0]
            payload = payload[4:]
            
        if message_type == MessageType.SERVER_FULL_RESPONSE:
            response.payload_size = struct.unpack('>I', payload[:4])[0]
            payload = payload[4:]
        elif message_type == MessageType.SERVER_ERROR_RESPONSE:
            response.code = struct.unpack('>i', payload[:4])[0]
            response.payload_size = struct.unpack('>I', payload[4:8])[0]
            payload = payload[8:]
            
        if not payload:
            return response
            
        if message_compression == CompressionType.GZIP:
            try:
                payload = CommonUtils.gzip_decompress(payload)
            except Exception as e:
                logger.error(f"Failed to decompress payload: {e}")
                return response
                
        try:
            if serialization_method == SerializationType.JSON:
                response.payload_msg = json.loads(payload.decode('utf-8'))
        except Exception as e:
            logger.error(f"Failed to parse payload: {e}")
            
        return response

class MicrophoneRecorder:
    def __init__(self, sample_rate: int = MIC_SAMPLE_RATE, chunk_size: int = 1024, device_index: int = None):
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size
        self.device_index = device_index
        self.audio = pyaudio.PyAudio()
        self.stream = None
        self.is_recording = False
        
        # 列出所有音频设备，帮助调试
        self._list_audio_devices()
        
    def _list_audio_devices(self):
        """列出所有音频设备"""
        logger.info("Available audio devices:")
        for i in range(self.audio.get_device_count()):
            device_info = self.audio.get_device_info_by_index(i)
            if device_info.get('maxInputChannels', 0) > 0:
                logger.info(f"  Device {i}: {device_info['name']} (Input channels: {device_info['maxInputChannels']})")
        
    def start_recording(self):
        """开始录制音频"""
        self.is_recording = True
        try:
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device_index,  # 指定设备索引
                frames_per_buffer=self.chunk_size
            )
            logger.info(f"Microphone recording started on device {self.device_index}")
        except Exception as e:
            logger.error(f"Failed to start recording on device {self.device_index}: {e}")
            # 尝试使用默认设备
            logger.info("Trying default device...")
            self.stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
            logger.info("Microphone recording started on default device")
        
    def stop_recording(self):
        """停止录制音频"""
        self.is_recording = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.audio.terminate()
        logger.info("Microphone recording stopped")
        
    def read_chunk(self) -> bytes:
        """读取一个音频块"""
        if self.stream and self.is_recording:
            try:
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                return data
            except Exception as e:
                logger.error(f"Error reading audio chunk: {e}")
                return b''
        return b''

class RealTimeAsrClient:
    def __init__(self, url: str, segment_duration: int = 200, device_index: int = None):
        self.seq = 1
        self.url = url
        self.segment_duration = segment_duration
        self.device_index = device_index
        self.conn = None
        self.session = None
        self.recorder = MicrophoneRecorder(device_index=device_index)
        self.result_text = ""
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        self.recorder.stop_recording()
        if self.conn and not self.conn.closed:
            await self.conn.close()
        if self.session and not self.session.closed:
            await self.session.close()
        
    def get_segment_size(self) -> int:
        """计算分段大小"""
        channel_num = 1
        samp_width = 2  # 16bit = 2字节
        frame_rate = DEFAULT_SAMPLE_RATE
        size_per_sec = channel_num * samp_width * frame_rate
        segment_size = size_per_sec * self.segment_duration // 1000
        return segment_size
        
    async def create_connection(self) -> None:
        headers = RequestBuilder.new_auth_headers()
        try:
            self.conn = await self.session.ws_connect(
                self.url,
                headers=headers
            )
            logger.info(f"Connected to {self.url}")
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            raise
            
    async def send_full_client_request(self) -> None:
        request = RequestBuilder.new_full_client_request(self.seq)
        self.seq += 1
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
            
    async def process_audio_stream(self) -> AsyncGenerator[AsrResponse, None]:
        """处理实时音频流"""
        segment_size = self.get_segment_size()
        buffer = bytearray()
        
        # 开始录制
        self.recorder.start_recording()
        
        try:
            while self.recorder.is_recording:
                # 读取麦克风数据
                raw_audio = self.recorder.read_chunk()
                if not raw_audio:
                    await asyncio.sleep(0.01)
                    continue
                
                # 重采样48kHz -> 16kHz
                resampled_audio = AudioUtils.resample_audio(
                    raw_audio, 
                    MIC_SAMPLE_RATE, 
                    DEFAULT_SAMPLE_RATE
                )
                
                # 添加到缓冲区
                buffer.extend(resampled_audio)
                
                # 当缓冲区有足够数据时发送
                while len(buffer) >= segment_size:
                    segment = bytes(buffer[:segment_size])
                    buffer = buffer[segment_size:]
                    
                    request = RequestBuilder.new_audio_only_request(
                        self.seq, 
                        segment,
                        is_last=False
                    )
                    await self.conn.send_bytes(request)
                    self.seq += 1
                    
                    # 接收响应
                    try:
                        msg = await asyncio.wait_for(self.conn.receive(), timeout=0.1)
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            response = ResponseParser.parse_response(msg.data)
                            yield response
                    except asyncio.TimeoutError:
                        pass
                    except Exception as e:
                        logger.error(f"Error receiving message: {e}")
                        
                await asyncio.sleep(0.01)
                
        except Exception as e:
            logger.error(f"Error in audio processing: {e}")
            raise
        finally:
            # 发送剩余数据
            if buffer:
                request = RequestBuilder.new_audio_only_request(
                    self.seq, 
                    bytes(buffer),
                    is_last=True
                )
                await self.conn.send_bytes(request)
                
    async def recv_messages(self) -> AsyncGenerator[AsrResponse, None]:
        """接收消息"""
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
            
    def update_result_file(self, text: str):
        """更新结果文件"""
        try:
            with open('a.txt', 'w', encoding='utf-8') as f:
                f.write(text)
            logger.info(f"Updated result file with: {text}")
        except Exception as e:
            logger.error(f"Failed to update result file: {e}")
            
    async def execute(self) -> AsyncGenerator[AsrResponse, None]:
        """执行实时语音识别"""
        if not self.url:
            raise ValueError("URL is empty")
            
        self.seq = 1
        
        try:
            # 创建WebSocket连接
            await self.create_connection()
            
            # 发送完整客户端请求
            await self.send_full_client_request()
            
            # 启动音频处理和接收任务
            import asyncio
            audio_task = asyncio.create_task(self._process_audio())
            recv_task = asyncio.create_task(self._receive_responses())
            
            # 等待任务完成
            try:
                await asyncio.gather(audio_task, recv_task)
            except Exception as e:
                logger.error(f"Task error: {e}")
                
        except Exception as e:
            logger.error(f"Error in ASR execution: {e}")
            raise
        finally:
            self.recorder.stop_recording()
            if self.conn:
                await self.conn.close()
                
    async def _process_audio(self):
        """处理音频流"""
        async for response in self.process_audio_stream():
            pass
            
    async def _receive_responses(self) -> AsyncGenerator[AsrResponse, None]:
        """接收并处理响应"""
        async for response in self.recv_messages():
            # 提取识别结果
            if (response.payload_msg and 
                'result' in response.payload_msg and 
                'text' in response.payload_msg['result']):
                
                text = response.payload_msg['result']['text']
                if text:
                    self.result_text = text
                    self.update_result_file(text)
                    
            yield response

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Real-time ASR WebSocket Client")
    parser.add_argument("--url", type=str, 
                       default="wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream", 
                       help="WebSocket URL")
    parser.add_argument("--seg-duration", type=int, default=200, 
                       help="Audio duration(ms) per packet, default:200")
    parser.add_argument("--device-index", type=int, default=None,
                       help="Microphone device index, default:None (use default device)")
    
    args = parser.parse_args()
    
    async with RealTimeAsrClient(args.url, args.seg_duration, args.device_index) as client:
        try:
            print("Real-time speech recognition started. Speak into your microphone...")
            print("Results will be saved to a.txt")
            print("Press Ctrl+C to stop")
            
            # 修复：直接调用execute()而不是在async for中使用
            async for response in client._receive_responses():
                if response.payload_msg:
                    logger.info(f"Received response: {json.dumps(response.to_dict(), indent=2, ensure_ascii=False)}")
                    
        except KeyboardInterrupt:
            print("\nStopping real-time speech recognition...")
        except Exception as e:
            logger.error(f"ASR processing failed: {e}")

if __name__ == "__main__":
    # 安装依赖命令：
    # pip install pyaudio numpy aiohttp
    asyncio.run(main())