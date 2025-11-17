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
    整合接收器：同时接收表情数据和语音关键词，实现优先级逻辑
    索引号规则：
    - 语音关键词优先级最高：左边→4，右边→5，挥手→7
    - 表情数据：0-3（原有逻辑）
    - 默认值：3（其他）
    """

    def __init__(self, emotion_host="127.0.0.1", emotion_port=5555, voice_host="127.0.0.1", voice_port=5557):
        # 表情接收器
        self.emotion_receiver = EmotionReceiver(emotion_host, emotion_port)

        # 语音关键词接收配置
        self.voice_host = voice_host
        self.voice_port = voice_port
        self.voice_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.voice_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.voice_sock.settimeout(1.0)

        # 运行状态
        self.running = False
        self.receive_thread = None

        # 最新状态
        self._latest_emotion_index = 3  # 默认表情索引
        self._latest_voice_keyword: Optional[str] = None  # 最新语音关键词
        self._voice_keyword_ts = 0.0  # 关键词时间戳

        # ★ 调整：放宽语音关键词有效期
        self._voice_timeout = 8.0  # 秒（原来 5.0）

        # ★ 新增：指令“粘滞保持”（防止瞬时回落）
        self._command_hold_secs = 2.0
        self._last_cmd_index: Optional[int] = None
        self._last_cmd_ts: float = 0.0

        # 线程安全锁
        self._lock = threading.Lock()

    def start(self):
        """启动接收线程"""
        if self.running:
            return

        try:
            # 启动表情接收器
            self.emotion_receiver.start()

            # 绑定语音关键词接收端口
            self.voice_sock.bind((self.voice_host, self.voice_port))
            self.running = True

            # 启动接收线程
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()

            print(f"[IntegratedReceiver] 已启动，监听表情端口 {self.emotion_receiver.port}，语音端口 {self.voice_port}")

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

    def _keyword_to_index(self, kw: Optional[str]) -> Optional[int]:
        if kw == "left":
            return 4
        if kw == "right":
            return 5
        if kw == "wave":
            return 7
        if kw == "nod":
            return 8
        return None

    def _receive_loop(self):
        """持续接收UDP数据"""
        while self.running:
            # 处理语音关键词数据
            try:
                data, addr = self.voice_sock.recvfrom(1024)
                try:
                    voice_data = json.loads(data.decode('utf-8'))

                    if voice_data.get('type') == 'voice_keyword':
                        keyword = voice_data.get('keyword')
                        timestamp = voice_data.get('timestamp', time.time())

                        with self._lock:
                            self._latest_voice_keyword = keyword
                            self._voice_keyword_ts = timestamp
                            idx = self._keyword_to_index(keyword)
                            if idx is not None:
                                self._last_cmd_index = idx
                                self._last_cmd_ts = timestamp

                        print(f"[语音关键词] 收到关键词: {keyword}, 时间: {timestamp}")

                except json.JSONDecodeError:
                    print(f"[IntegratedReceiver] 收到非JSON语音数据: {data.decode('utf-8', errors='replace')}")
                except Exception as e:
                    print(f"[IntegratedReceiver] 处理语音数据出错: {e}")

            except socket.timeout:
                pass  # 正常超时，继续循环
            except Exception as e:
                if self.running:
                    print(f"[IntegratedReceiver] 接收语音数据出错: {e}")
                break

            # 短暂休眠避免CPU占用过高
            time.sleep(0.01)

    def get_final_index(self):
        """
        获取最终索引号（考虑优先级）
        ① 指令保持窗口优先
        ② 语音关键词在有效期内（优先级最高）
        ③ 回落到表情索引
        """
        now = time.time()

        with self._lock:
            # ① 粘滞保持：刚触发的指令，至少保持 _command_hold_secs 秒
            if (self._last_cmd_index is not None and
                (now - self._last_cmd_ts) <= self._command_hold_secs):
                return self._last_cmd_index

            # ② 语音关键词仍在有效期 → 直接用语音索引（优先级最高）
            if (self._latest_voice_keyword and
                (now - self._voice_keyword_ts) <= self._voice_timeout):
                idx = self._keyword_to_index(self._latest_voice_keyword)
                if idx is not None:
                    return idx

            # ③ 否则使用表情索引
            return self.emotion_receiver.get_latest_emotion_index()

    async def get_final_index_async(self):
        """异步接口获取最终索引号"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.get_final_index)

    def get_detailed_status(self):
        """获取详细状态信息（用于调试）"""
        current_time = time.time()

        with self._lock:
            emotion_index = self.emotion_receiver.get_latest_emotion_index()
            voice_active = (self._latest_voice_keyword and
                           (current_time - self._voice_keyword_ts) <= self._voice_timeout)
            voice_keyword = self._latest_voice_keyword if voice_active else None
            final_index = self.get_final_index()
            hold_remaining = max(0.0, self._command_hold_secs - (current_time - self._last_cmd_ts)) \
                             if self._last_cmd_index is not None else 0.0

            return {
                "emotion_index": emotion_index,
                "voice_keyword": voice_keyword,
                "voice_active": bool(voice_active),
                "final_index": final_index,
                "voice_time_remaining": (max(0.0, self._voice_timeout - (current_time - self._voice_keyword_ts))
                                         if voice_active else 0.0),
                "hold_remaining": hold_remaining
            }