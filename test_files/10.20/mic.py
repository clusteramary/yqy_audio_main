# main_mic_usage_example.py
import asyncio

from mic_receiver import MicReceiver


class MainMicApplication:
    def __init__(self):
        self.receiver = MicReceiver(port=5558)  # 与 audio_manager 中的 MIC 发送端口一致
        self.running = False
        self._last_index = None

    async def run(self):
        """仅监听并打印 MIC 指令（递/收话筒）"""
        self.receiver.start()
        self.running = True

        try:
            while self.running:
                index = self.receiver.get_mic_index()
                await self.execute_mic_action_based_on_index(index)
                await asyncio.sleep(0.1)  # 100ms更新一次
        except KeyboardInterrupt:
            print("收到停止信号...")
        finally:
            self.running = False
            self.receiver.stop()

    async def execute_mic_action_based_on_index(self, index: int):
        """根据 MIC 索引号打印动作"""
        actions = {
            0: "无 mic 指令（空闲）",
            6: "收话筒操作 - 执行收话筒动作",
            9: "递话筒操作 - 执行递话筒动作",
        }
        action = actions.get(index, "未知 mic 索引")

        # 只在索引变化时打印
        if self._last_index != index:
            print(f"[MIC] 索引变化: {self._last_index} → {index}，执行: {action}")
        self._last_index = index

        # TODO: 在这里添加具体的硬件控制逻辑（如：机械臂递/收话筒等）


if __name__ == "__main__":
    app = MainMicApplication()
    asyncio.run(app.run())