# integrated_receiver.py
import asyncio
import json
import socket
import threading
import time
from typing import Optional

from emotion_receiver import EmotionReceiver


class IntegratedReceiver:
    """
    整合接收器：同时接收表情数据、语音关键词与手势索引，实现优先级逻辑
    最终索引优先级（高 → 低）：
      1) 手势索引：拿话筒 → 6，结束挥手 → 7
      2) 语音关键词：左边 → 4，右边 → 5
      3) 表情索引：0-3（见 EmotionReceiver）
      4) 默认值：3（其他）
    """

    def __init__(self,
                 emotion_host: str = "127.0.0.1",
                 emotion_port: int = 5555,
                 voice_host: str = "127.0.0.1",
                 voice_port: int = 5557):
        # 表情接收器
        self.emotion_receiver = EmotionReceiver(emotion_host, emotion_port)

        # 语音/手势 UDP 接收配置（共用一个端口）
        self.voice_host = voice_host
        self.voice_port = voice_port
        self.voice_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.voice_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.voice_sock.settimeout(1.0)

        # 运行状态
        self.running = False
        self.receive_thread: Optional[threading.Thread] = None

        # 最新状态
        self._latest_emotion_index = 3  # 默认表情索引（兜底）
        self._latest_voice_keyword: Optional[str] = None  # "left" / "right"
        self._voice_keyword_ts: float = 0.0               # 关键词时间戳
        self._voice_timeout: float = 5.0                  # 语音关键词有效期（秒）

        # 手势索引（拿话筒/结束挥手）优先级最高
        self._latest_gesture_index: Optional[int] = None  # 6 / 7
        self._gesture_ts: float = 0.0
        self._gesture_timeout: float = 8.0                # 手势索引有效期（秒）

        # 线程安全锁
        self._lock = threading.Lock()

    def start(self):
        """启动接收线程"""
        if self.running:
            return

        try:
            # 启动表情接收器
            self.emotion_receiver.start()

            # 绑定语音/手势接收端口
            self.voice_sock.bind((self.voice_host, self.voice_port))
            self.running = True

            # 启动接收线程
            self.receive_thread = threading.Thread(
                target=self._receive_loop, daemon=True
            )
            self.receive_thread.start()

            print(f"[IntegratedReceiver] 已启动，监听表情端口 {self.emotion_receiver.port}，语音/手势端口 {self.voice_port}")

        except Exception as e:
            print(f"[IntegratedReceiver] 启动失败: {e}")

    def stop(self):
        """停止接收"""
        self.running = False
        try:
            self.emotion_receiver.stop()
        except Exception:
            pass
        try:
            if self.voice_sock:
                self.voice_sock.close()
        except Exception:
            pass
        print("[IntegratedReceiver] 已停止")

    def _receive_loop(self):
        """持续接收 UDP 数据（语音关键词/手势索引）"""
        while self.running:
            # 处理语音关键词/手势索引数据
            try:
                data, addr = self.voice_sock.recvfrom(1024)
                try:
                    voice_data = json.loads(data.decode("utf-8"))

                    # 语音关键词（左/右）保持原逻辑
                    if voice_data.get("type") == "voice_keyword":
                        keyword = voice_data.get("keyword")
                        timestamp = voice_data.get("timestamp", time.time())
                        with self._lock:
                            self._latest_voice_keyword = keyword
                            self._voice_keyword_ts = timestamp
                        print(f"[语音关键词] 收到关键词: {keyword}, 时间: {timestamp}")

                    # === 新增：手势索引（6/7） ===
                    elif voice_data.get("type") == "gesture_index":
                        idx = int(voice_data.get("index", -1))
                        timestamp = voice_data.get("timestamp", time.time())
                        with self._lock:
                            self._latest_gesture_index = idx
                            self._gesture_ts = timestamp
                        print(f"[手势索引] 收到 index={idx}, 时间: {timestamp}")

                except json.JSONDecodeError:
                    print(f"[IntegratedReceiver] 收到非JSON语音/手势数据: {data.decode('utf-8', errors='replace')}")
                except Exception as e:
                    print(f"[IntegratedReceiver] 处理语音/手势数据出错: {e}")

            except socket.timeout:
                pass  # 正常超时，继续循环
            except Exception as e:
                if self.running:
                    print(f"[IntegratedReceiver] 接收语音/手势数据出错: {e}")
                break

            # 短暂休眠避免CPU占用过高
            time.sleep(0.01)

    def get_final_index(self) -> int:
        """
        获取最终索引号（考虑优先级）
        规则：手势(6/7) > 语音关键词(4/5) > 表情(0-3)
        """
        current_time = time.time()

        with self._lock:
            # 1) 手势索引（最高优先级）
            if (
                self._latest_gesture_index is not None
                and (current_time - self._gesture_ts) <= self._gesture_timeout
            ):
                return self._latest_gesture_index

            # 2) 语音关键词（次高）
            if (
                self._latest_voice_keyword
                and (current_time - self._voice_keyword_ts) <= self._voice_timeout
            ):
                if self._latest_voice_keyword == "left":
                    return 4
                elif self._latest_voice_keyword == "right":
                    return 5

            # 3) 表情索引（默认）
            return self.emotion_receiver.get_latest_emotion_index()

    async def get_final_index_async(self) -> int:
        """异步接口获取最终索引号"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get_final_index)

    def get_detailed_status(self) -> dict:
        """
        获取详细状态信息（用于调试）
        注意：为避免死锁，final_index 在释放锁后单独获取
        """
        current_time = time.time()

        with self._lock:
            # 表情状态
            emotion_index = self.emotion_receiver.get_latest_emotion_index()

            # 语音关键词状态
            voice_active = (
                self._latest_voice_keyword
                and (current_time - self._voice_keyword_ts) <= self._voice_timeout
            )
            voice_keyword = self._latest_voice_keyword if voice_active else None
            voice_time_remaining = (
                max(0.0, self._voice_timeout - (current_time - self._voice_keyword_ts))
                if voice_active
                else 0.0
            )

            # 手势索引状态
            gesture_active = (
                self._latest_gesture_index is not None
                and (current_time - self._gesture_ts) <= self._gesture_timeout
            )
            gesture_index = self._latest_gesture_index if gesture_active else None
            gesture_time_remaining = (
                max(0.0, self._gesture_timeout - (current_time - self._gesture_ts))
                if gesture_active
                else 0.0
            )

        # 避免在持锁状态下再次获取（内部也会尝试加锁）
        final_index = self.get_final_index()

        return {
            "gesture_index": gesture_index,
            "gesture_active": bool(gesture_active),
            "gesture_time_remaining": gesture_time_remaining,
            "emotion_index": emotion_index,
            "voice_keyword": voice_keyword,
            "voice_active": bool(voice_active),
            "voice_time_remaining": voice_time_remaining,
            "final_index": final_index,
        }