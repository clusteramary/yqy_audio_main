# udp_receiver_test.py
import json
import socket
import threading
import time


class EmotionReceiver:
    """UDP 情绪数据接收器"""
    
    def __init__(self, host="127.0.0.1", port=5555):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.running = False
        self.receive_thread = None
        
    def start(self):
        """启动接收器"""
        if self.running:
            return
            
        try:
            self.sock.bind((self.host, self.port))
            self.running = True
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
            print(f"开始监听 UDP 情绪数据，地址: {self.host}:{self.port}")
        except Exception as e:
            print(f"启动接收器失败: {e}")
            
    def stop(self):
        """停止接收器"""
        self.running = False
        if self.sock:
            self.sock.close()
        print("已停止 UDP 情绪数据接收")
            
    def _receive_loop(self):
        """接收循环"""
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1024)  # 缓冲区大小
                try:
                    # 解析 JSON 数据
                    emotion_data = json.loads(data.decode('utf-8'))
                    
                    # 格式化输出
                    ts = emotion_data.get('ts', 0)
                    has_face = emotion_data.get('has_face', False)
                    dominant_emotion = emotion_data.get('dominant_emotion', '未知')
                    
                    # 转换为可读时间
                    readable_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
                    
                    print(f"\n[{readable_time}] 来自 {addr}:")
                    print(f"  检测到人脸: {'是' if has_face else '否'}")
                    
                    if has_face:
                        print(f"  主要情绪: {dominant_emotion}")
                        # 打印所有情绪及其百分比
                        emotions = emotion_data.get('emotion', {})
                        for emotion, value in emotions.items():
                            print(f"    {emotion}: {value:.2f}%")
                    else:
                        print("  未检测到人脸")
                        
                except json.JSONDecodeError:
                    print(f"收到非JSON数据: {data.decode('utf-8', errors='replace')}")
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:  # 仅在运行状态下打印错误
                    print(f"接收数据时出错: {e}")
                break
                
    def set_timeout(self, timeout):
        """设置接收超时时间"""
        self.sock.settimeout(timeout)


def main():
    """主函数"""
    receiver = EmotionReceiver()
    
    try:
        receiver.start()
        print("按 Ctrl+C 停止接收...")
        
        # 保持主线程运行
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n正在停止...")
    finally:
        receiver.stop()


if __name__ == "__main__":
    main()