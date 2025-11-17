# test_integrated_receiver.py
import asyncio
import time

from emotion_receiver import IntegratedReceiver


async def test_integrated_receiver():
    """测试整合接收器"""
    receiver = IntegratedReceiver()
    
    # 启动接收器
    receiver.start()
    
    try:
        # 持续运行并获取最新的整合索引
        for i in range(20):  # 运行20次，约40秒
            final_index = await receiver.get_final_index_async()
            status = receiver.get_detailed_status()
            
            print(f"[{i+1}] 最终索引: {final_index}")
            print(f"     表情索引: {status['emotion_index']}")
            print(f"     语音关键词: {status['voice_keyword']} (活跃: {status['voice_active']})")
            if status['voice_active']:
                print(f"     语音剩余时间: {status['voice_time_remaining']:.1f}秒")
            print("-" * 50)
            
            await asyncio.sleep(2)  # 每2秒获取一次
            
    except KeyboardInterrupt:
        print("\n正在停止测试...")
    finally:
        receiver.stop()


if __name__ == "__main__":
    # 运行测试
    asyncio.run(test_integrated_receiver())