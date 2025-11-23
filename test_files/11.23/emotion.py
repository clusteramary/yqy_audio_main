# main_usage_example.py
import asyncio

from integrated_receiver import IntegratedReceiver


class MainApplication:
    def __init__(self):
        self.receiver = IntegratedReceiver()
        self.running = False
        self._last_index = None

    async def run(self):
        """主应用逻辑"""
        self.receiver.start()
        self.running = True

        try:
            while self.running:
                # 获取最终索引号
                index = await self.receiver.get_final_index_async()

                # 根据索引号执行相应动作
                await self.execute_action_based_on_index(index)

                # 控制更新频率
                await asyncio.sleep(0.1)  # 100ms更新一次

        except KeyboardInterrupt:
            print("收到停止信号...")
        finally:
            self.running = False
            self.receiver.stop()

    async def execute_action_based_on_index(self, index: int):
        """根据索引号执行动作"""
        actions = {
            0: "未检测到人脸 - 保持待机",
            1: "高兴/惊讶/中性 - 友好互动",
            2: "悲伤/恐惧 - 安慰模式",
            3: "其他情绪 - 一般互动",
            4: "检测到'左边'指令 - 向左移动",
            5: "检测到'右边'指令 - 向右移动",
            7: "检测到'挥手'指令 - 执行挥手动作",  # ★ 新增：挥手
            8: "检测到'点头'指令 - 向前点头",
            10: "检测到'击掌'指令 - 进行击掌",
            11: "检测到'开始'指令 - 进行开始动作",
            12: "检测到'结束'指令 - 进行结束动作",
            13: "检测到'握手'指令 - 进行握手动作",
            # 6: "递话筒操作 - 执行递话筒动作",  # 新增：递话筒操作
            # 9: "收话筒操作 - 执行收话筒动作",  # 新增：收话筒操作
        }

        action = actions.get(index, "未知索引")

        # 只在索引变化时打印，避免输出过多
        if self._last_index != index:
            print(f"索引变化: {self._last_index} → {index}, 执行: {action}")

        self._last_index = index

        # TODO: 在这里添加具体的硬件控制逻辑
        # 例如：控制机器人移动、改变LED颜色等


# 运行主应用
if __name__ == "__main__":
    app = MainApplication()
    asyncio.run(app.run())
