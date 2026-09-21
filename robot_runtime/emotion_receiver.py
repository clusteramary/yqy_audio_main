import asyncio
import json
import socket
import threading
import time


class EmotionReceiver:
    """
    UDP 情绪数据接收器（持续运行）
    提供接口 `get_latest_emotion_index()` 获取最新的情绪索引：
    0 - 未检测到人脸
    1 - happy/surprise/neutral 占比最高
    2 - sad/fear 占比最高
    3 - 其他
    """

    def __init__(self, host="127.0.0.1", port=5555):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(1.0)

        self.running = False
        self.receive_thread = None

        # 最新情绪索引，默认3（其他）
        self._latest_index = 3
        self._lock = threading.Lock()  # 线程安全锁

    def start(self):
        """启动接收线程"""
        if self.running:
            return
        try:
            self.sock.bind((self.host, self.port))
            self.running = True
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
            print(f"[EmotionReceiver] 已启动 UDP 接收，监听 {self.host}:{self.port}")
        except Exception as e:
            print(f"[EmotionReceiver] 启动失败: {e}")

    def stop(self):
        """停止接收"""
        self.running = False
        if self.sock:
            self.sock.close()
        print("[EmotionReceiver] 已停止 UDP 接收")

    def _receive_loop(self):
        """持续接收 UDP 数据"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                try:
                    emotion_data = json.loads(data.decode('utf-8'))

                    has_face = emotion_data.get('has_face', False)
                    dominant_emotion = emotion_data.get('dominant_emotion', '未知')
                    emotions = emotion_data.get('emotion', {})

                    # 默认分类为其他
                    emotion_index = 3

                    if not has_face:
                        emotion_index = 0
                    elif emotions:
                        # 找占比最高的情绪
                        max_emotion = max(emotions, key=emotions.get)
                        max_value = emotions.get(max_emotion, 0)

                        if max_emotion in ['happy', 'surprise', 'neutral'] and max_value > 50:
                            emotion_index = 1
                        elif max_emotion in ['sad', 'fear'] and max_value > 50:
                            emotion_index = 2

                    # 更新最新情绪索引（线程安全）
                    with self._lock:
                        self._latest_index = emotion_index

                    # 打印信息
                    ts = emotion_data.get('ts', time.time())
                    readable_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
                    # print(f"\n[{readable_time}] 来自 {addr}:")
                    # print(f"  检测到人脸: {'是' if has_face else '否'}")
                    if has_face:
                        # print(f"  主要情绪: {dominant_emotion}")
                        # print(f"  情绪分类索引: {emotion_index}")
                        for emo, val in emotions.items():
                            print(f"    {emo}: {val:.2f}%")
                    else:
                        print("  未检测到人脸")
                        # print(f"  情绪分类索引: {emotion_index}")

                except json.JSONDecodeError:
                    print(f"[EmotionReceiver] 收到非JSON数据: {data.decode('utf-8', errors='replace')}")

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[EmotionReceiver] 接收数据出错: {e}")
                break

    def get_latest_emotion_index(self):
        """获取最新情绪索引（线程安全）"""
        with self._lock:
            return self._latest_index

    async def get_latest_emotion_index_async(self):
        """异步接口获取最新情绪索引"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get_latest_emotion_index)