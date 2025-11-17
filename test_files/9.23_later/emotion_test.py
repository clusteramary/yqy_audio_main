import asyncio

from emotion_receiver import EmotionReceiver


async def test_emotion_receiver():
    """测试情绪接收器，异步获取最新情绪索引"""
    receiver = EmotionReceiver()

    # 启动接收器
    receiver.start()

    try:
        # 持续运行并获取最新的情绪索引
        for _ in range(5):
            emotion_index = await receiver.get_latest_emotion_index_async()
            print(f"[Test] 当前情绪索引: {emotion_index}")
            await asyncio.sleep(2)  # 每2秒获取一次情绪索引
    except KeyboardInterrupt:
        print("\n正在停止测试...")
    finally:
        receiver.stop()


if __name__ == "__main__":
    # 运行测试
    asyncio.run(test_emotion_receiver())