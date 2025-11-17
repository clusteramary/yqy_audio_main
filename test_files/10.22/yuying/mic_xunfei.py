import audioop  # 用于音频重采样
import base64
import datetime
import hashlib
import hmac
import json
import signal
import ssl
import sys
import threading
import time
from time import mktime
from wsgiref.handlers import format_date_time

import pyaudio
import websocket

# 替换为你的科大讯飞密钥
APPID = "3342220c"
APIKey = "1f22abc2f791de7bef807e752d8a60ee"
APISecret = "OWViNTEyODA1ZGIzNThjYjkzMzNjYWFl"  # 请核对是否为40位

# WebSocket服务地址
WS_URL = "wss://ws-api.xfyun.cn/v2/iat"

# 音频参数 - 根据你的测试代码调整
FORMAT = pyaudio.paInt16  # 16位深度
CHANNELS = 1              # 单声道
RATE_MIC = 48000          # 麦克风采样率
RATE_TARGET = 16000       # 目标采样率（科大讯飞要求16kHz）
CHUNK_MIC = 960           # 48000Hz下的40ms帧大小
CHUNK_TARGET = 640        # 16000Hz下的40ms帧大小
DEVICE_INDEX = 10         # 根据你的测试，使用设备索引10

# 生成WebSocket URL
def create_url():
    now = datetime.datetime.now()
    date = format_date_time(mktime(now.timetuple()))
    signature_origin = "host: ws-api.xfyun.cn\n" + "date: " + date + "\n" + "GET /v2/iat HTTP/1.1"
    signature_sha = hmac.new(APISecret.encode('utf-8'), signature_origin.encode('utf-8'), digestmod=hashlib.sha256).digest()
    signature_sha = base64.b64encode(signature_sha).decode('utf-8')
    authorization_origin = f'api_key="{APIKey}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_sha}"'
    authorization = base64.b64encode(authorization_origin.encode('utf-8')).decode('utf-8')
    v = {"authorization": authorization, "date": date, "host": "ws-api.xfyun.cn"}
    from urllib.parse import urlencode
    return WS_URL + "?" + urlencode(v)

# WebSocket客户端类
class IatWebSocketClient:
    def __init__(self):
        self.ws = None
        self.p = pyaudio.PyAudio()
        self.stream = None
        self.connected = False
        self.recognizing = False

    def open_audio_stream(self):
        """打开麦克风音频流 - 使用指定的设备索引和48000Hz采样率"""
        try:
            # 检查设备是否可用
            device_info = self.p.get_device_info_by_index(DEVICE_INDEX)
            print(f"使用音频设备: {device_info['name']}")
            print(f"采样率: {RATE_MIC}Hz -> 重采样到 {RATE_TARGET}Hz")
            
            self.stream = self.p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE_MIC,
                input=True,
                input_device_index=DEVICE_INDEX,
                frames_per_buffer=CHUNK_MIC
            )
            print("音频流已打开")
        except Exception as e:
            print(f"打开音频流失败: {e}")
            # 如果指定设备失败，尝试使用默认设备
            try:
                print("尝试使用默认音频设备...")
                self.stream = self.p.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE_MIC,
                    input=True,
                    frames_per_buffer=CHUNK_MIC
                )
                print("默认音频设备已打开")
            except Exception as e2:
                print(f"打开默认音频设备也失败: {e2}")
                raise

    def resample_audio(self, audio_data):
        """将48000Hz音频重采样到16000Hz"""
        try:
            # 使用audioop进行重采样
            # 从48000Hz重采样到16000Hz，采样率比例为3:1
            resampled_data = audioop.ratecv(audio_data, 2, 1, RATE_MIC, RATE_TARGET, None)
            return resampled_data[0]
        except Exception as e:
            print(f"重采样失败: {e}")
            return audio_data  # 如果重采样失败，返回原始数据

    def close_audio_stream(self):
        """关闭音频流"""
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            print("音频流已关闭")
        self.p.terminate()
        print("PyAudio已终止")

    def on_message(self, ws, message):
        """接收并打印识别结果并保存到a.txt"""
        try:
            data = json.loads(message)
            if data["code"] != 0:
                print(f"错误: {data['message']} (code: {data['code']})")
                self.ws.close()
            else:
                result = data["data"]["result"]["ws"]
                text = "".join([item["cw"][0]["w"] for item in result])
                print(f"实时识别结果: {text}")
                # 将识别结果保存到 a.txt 文件中，每次追加一行
                with open('a.txt', 'a', encoding='utf-8') as file:
                    file.write(text + '\n')
        except Exception as e:
            print(f"消息解析错误: {e}")

    def on_error(self, ws, error):
        print(f"WebSocket错误: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        print("WebSocket连接已关闭")
        self.connected = False
        self.recognizing = False
        self.close_audio_stream()

    def on_open(self, ws):
        print("WebSocket连接已建立，开始录音和识别...")
        self.connected = True
        self.recognizing = True
        self.send_first_frame()

    def send_first_frame(self):
        """发送首帧并启动音频数据发送"""
        self.open_audio_stream()
        threading.Thread(target=self.send_audio_data).start()

    def send_audio_data(self):
        """实时发送音频数据"""
        status = 0  # 0: 首帧, 1: 中间帧, 2: 尾帧
        try:
            while self.recognizing:
                # 从48000Hz麦克风读取数据
                audio_data_48k = self.stream.read(CHUNK_MIC, exception_on_overflow=False)
                
                # 重采样到16000Hz
                audio_data_16k = self.resample_audio(audio_data_48k)
                
                audio_base64 = base64.b64encode(audio_data_16k).decode('utf-8')
                if status == 0:  # 首帧
                    d = {
                        "common": {"app_id": APPID},
                        "business": {"domain": "iat", "language": "zh_cn", "accent": "mandarin", "vinfo": 1, "vad_eos": 10000},
                        "data": {"status": 0, "format": "audio/L16;rate=16000", "audio": audio_base64, "encoding": "raw"}
                    }
                    self.ws.send(json.dumps(d))
                    status = 1
                else:  # 中间帧
                    d = {"data": {"status": 1, "format": "audio/L16;rate=16000", "audio": audio_base64, "encoding": "raw"}}
                    self.ws.send(json.dumps(d))
                time.sleep(0.04)  # 每40ms发送一次
        except Exception as e:
            print(f"发送音频数据失败: {e}")
            self.ws.close()

    def start(self):
        """启动WebSocket客户端"""
        ws_url = create_url()
        self.ws = websocket.WebSocketApp(ws_url,
                                         on_message=self.on_message,
                                         on_error=self.on_error,
                                         on_close=self.on_close)
        self.ws.on_open = self.on_open
        self.ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

    def stop(self):
        """停止识别并发送尾帧"""
        if self.recognizing:
            print("正在停止识别...")
            self.recognizing = False
            time.sleep(0.1)  # 等待当前音频数据发送完成
            
            # 发送尾帧
            try:
                audio_base64 = base64.b64encode(b'').decode('utf-8')  # 空音频
                d = {"data": {"status": 2, "format": "audio/L16;rate=16000", "audio": audio_base64, "encoding": "raw"}}
                self.ws.send(json.dumps(d))
                print("尾帧已发送")
            except Exception as e:
                print(f"发送尾帧失败: {e}")
            
            time.sleep(1)  # 等待服务端处理
            if self.ws:
                self.ws.close()

def signal_handler(sig, frame):
    """处理Ctrl+C信号"""
    print('\n接收到Ctrl+C，正在停止识别...')
    client.stop()
    sys.exit(0)

# 主程序
if __name__ == "__main__":
    client = IatWebSocketClient()
    
    # 注册信号处理器，用于捕获Ctrl+C
    signal.signal(signal.SIGINT, signal_handler)
    
    print("开始流式录音和识别...")
    print("按下 Ctrl+C 停止识别...")
    
    # 显示可用的音频设备信息（可选）
    p = pyaudio.PyAudio()
    print("可用的音频输入设备:")
    for i in range(p.get_device_count()):
        device_info = p.get_device_info_by_index(i)
        if device_info['maxInputChannels'] > 0:
            print(f"设备 {i}: {device_info['name']} (采样率: {device_info['defaultSampleRate']})")
    p.terminate()
    
    # 启动语音识别
    client.start()