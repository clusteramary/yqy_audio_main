# mic_receiver.py
import json
import socket
import threading
import time
from typing import Optional


class MicReceiver:
    """
    仅接收：递话筒/收话筒（独立端口 5558）。
    形成独立索引流（默认：send_microphone→6，release_microphone→9）。
    """
    def __init__(self, host="127.0.0.1", port=5558,
                 index_send: int = 6, index_release: int = 9,
                 command_hold_secs: float = 1.0):
        self.host = host
        self.port = port
        self.index_send = index_send
        self.index_release = index_release
        self.command_hold_secs = command_hold_secs

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.settimeout(1.0)

        self.running = False
        self.thread: Optional[threading.Thread] = None

        self._last_index: Optional[int] = None
        self._last_ts: float = 0.0
        self._lock = threading.Lock()

    def start(self):
        if self.running:
            return
        try:
            self.sock.bind((self.host, self.port))
            self.running = True
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            print(f"[MicReceiver] 已启动：监听 {self.host}:{self.port}")
        except Exception as e:
            print(f"[MicReceiver] 启动失败: {e}")

    def stop(self):
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        print("[MicReceiver] 已停止")

    def _loop(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)
                try:
                    obj = json.loads(data.decode("utf-8"))
                    if obj.get("type") == "mic_command":
                        cmd = obj.get("command")
                        now = obj.get("timestamp", time.time())
                        with self._lock:
                            if cmd == "send_microphone":
                                self._last_index = self.index_send
                                self._last_ts = now
                                print("[MIC] 收到 收话筒 → 索引", self._last_index)
                            elif cmd == "release_microphone":
                                self._last_index = self.index_release
                                self._last_ts = now
                                print("[MIC] 收到 递话筒 → 索引", self._last_index)
                except json.JSONDecodeError:
                    print(f"[MicReceiver] 非JSON数据: {data.decode('utf-8', errors='replace')}")
                except Exception as e:
                    print(f"[MicReceiver] 处理数据出错: {e}")
            except socket.timeout:
                pass
            except Exception as e:
                if self.running:
                    print(f"[MicReceiver] 接收出错: {e}")
                break
            time.sleep(0.01)

    def get_mic_index(self) -> int:
        """
        返回“话筒通道”的当前索引（在保持窗口内）。
        超时返回 0（你也可改成 None）。
        """
        now = time.time()
        with self._lock:
            if self._last_index is not None and (now - self._last_ts) <= self.command_hold_secs:
                return self._last_index
        return 0

    def get_detailed_status(self):
        now = time.time()
        with self._lock:
            hold_remaining = max(0.0, self.command_hold_secs - (now - self._last_ts)) \
                if self._last_index is not None else 0.0
            return {
                "last_index": self._last_index,
                "hold_remaining": hold_remaining
            }