import os
import socket
import threading


class UDPReceiver:
    def __init__(
        self, listen_ip="0.0.0.0", listen_port=8888, file_path="sauc_python/ctrl.txt"
    ):
        """
        初始化UDP接收器
        :param listen_ip: 监听IP地址
        :param listen_port: 监听端口
        :param file_path: 文件保存路径
        """
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.file_path = file_path
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = False

        # 确保目录存在
        self._ensure_directory()

    def _ensure_directory(self):
        """确保保存文件的目录存在"""
        directory = os.path.dirname(self.file_path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
            print(f"创建目录: {directory}")

    def _write_to_file(self, message):
        """
        将消息写入文件（覆盖式写入）
        :param message: 要写入的字符串
        """
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(message)
            print(f"消息已写入文件: {self.file_path}")
            return True
        except Exception as e:
            print(f"写入文件失败: {e}")
            return False

    def start_receiving(self):
        """开始接收消息"""
        try:
            # 绑定地址和端口
            self.sock.bind((self.listen_ip, self.listen_port))
            print(f"UDP接收器已启动，监听 {self.listen_ip}:{self.listen_port}")
            print(f"消息将保存至: {os.path.abspath(self.file_path)}")

            self.running = True

            while self.running:
                # 接收数据，缓冲区大小为1024字节
                data, addr = self.sock.recvfrom(1024)

                # 解码字节为字符串
                message = data.decode("utf-8")

                print(f"收到来自 {addr} 的消息: {message}")

                # 将接收到的字符串写入文件
                self._write_to_file(message)

                # 如果收到退出信号，停止接收
                if message.lower() == "exit":
                    print("收到退出信号，停止接收...")
                    self.stop_receiving()

        except Exception as e:
            print(f"接收错误: {e}")
        finally:
            self.close()

    def stop_receiving(self):
        """停止接收消息"""
        self.running = False

    def close(self):
        """关闭socket连接"""
        self.sock.close()
        print("UDP接收器已关闭")


# 使用示例
if __name__ == "__main__":
    # 创建接收器实例，指定文件路径
    receiver = UDPReceiver("0.0.0.0", 8889, "sauc_python/ctrl.txt")

    try:
        # 开始接收消息
        receiver.start_receiving()
    except KeyboardInterrupt:
        print("\n用户中断程序")
        receiver.stop_receiving()
