#!/usr/bin/env python3
"""
麦克风配置检查和测试工具

用于帮助配置双麦克风系统中的嘉宾麦克风和辅助记者麦克风。
"""

import os
import sys
import subprocess

# 设置环境变量（避免 PyAudio 初始化问题）
os.environ.setdefault('PA_ALSA_PLUGHW', '1')
os.environ.setdefault('JACK_NO_AUDIO_RESERVATION', '1')

import pyaudio


def list_pyaudio_devices():
    """列出所有 PyAudio 音频设备"""
    print("\n" + "=" * 60)
    print("PyAudio 音频设备列表")
    print("=" * 60)
    
    p = pyaudio.PyAudio()
    
    print("\n【输入设备（麦克风）】")
    print("-" * 40)
    
    input_devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            input_devices.append((i, info))
            print(f"  索引 {i}: {info['name']}")
            print(f"         采样率: {int(info['defaultSampleRate'])} Hz")
            print(f"         输入通道: {info['maxInputChannels']}")
            print()
    
    print("\n【输出设备（扬声器）】")
    print("-" * 40)
    
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxOutputChannels"] > 0:
            print(f"  索引 {i}: {info['name']}")
    
    p.terminate()
    
    return input_devices


def list_alsa_devices():
    """列出 ALSA 录音设备（Linux）"""
    print("\n" + "=" * 60)
    print("ALSA 录音设备列表（用于 ROS audio_capture）")
    print("=" * 60)
    
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(result.stdout)
            print("【提示】启动 ROS audio_capture 时使用：")
            print("  roslaunch audio_capture capture.launch device:=hw:卡号,设备号")
            print("  例如：roslaunch audio_capture capture.launch device:=hw:2,0")
        else:
            print("无法获取 ALSA 设备列表（可能不是 Linux 系统）")
    except FileNotFoundError:
        print("arecord 命令不存在（可能不是 Linux 系统或未安装 alsa-utils）")
    except Exception as e:
        print(f"获取 ALSA 设备失败: {e}")


def test_microphone(device_index: int, duration: float = 3.0):
    """测试指定麦克风是否能正常录音"""
    print(f"\n测试麦克风（索引 {device_index}）...")
    
    p = pyaudio.PyAudio()
    
    try:
        info = p.get_device_info_by_index(device_index)
        print(f"设备名称: {info['name']}")
        
        # 尝试打开设备
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=1600,
        )
        
        print(f"正在录音 {duration} 秒...")
        
        max_rms = 0
        chunks = int(16000 / 1600 * duration)
        
        for _ in range(chunks):
            data = stream.read(1600, exception_on_overflow=False)
            # 计算 RMS
            import struct
            samples = struct.unpack(f"{len(data)//2}h", data)
            rms = (sum(s**2 for s in samples) / len(samples)) ** 0.5
            max_rms = max(max_rms, rms)
            
            # 显示音量条
            bar_len = min(int(rms / 100), 50)
            bar = "█" * bar_len + "░" * (50 - bar_len)
            print(f"\r  音量: [{bar}] RMS: {rms:6.0f}", end="", flush=True)
        
        print()
        stream.stop_stream()
        stream.close()
        
        print(f"\n✓ 麦克风工作正常！最大 RMS: {max_rms:.0f}")
        
        if max_rms < 100:
            print("⚠ 警告：音量非常低，请检查麦克风是否正确连接或静音")
        
        return True
        
    except Exception as e:
        print(f"✗ 麦克风测试失败: {e}")
        return False
    finally:
        p.terminate()


def show_current_config():
    """显示当前的麦克风配置"""
    print("\n" + "=" * 60)
    print("当前 config.py 麦克风配置")
    print("=" * 60)
    
    try:
        import config
        
        guest_idx = getattr(config, "GUEST_MIC_INDEX", "未配置")
        assistant_idx = getattr(config, "ASSISTANT_MIC_INDEX", "未配置")
        
        print(f"\n  嘉宾麦克风索引 (GUEST_MIC_INDEX): {guest_idx}")
        print(f"  辅助记者麦克风索引 (ASSISTANT_MIC_INDEX): {assistant_idx}")
        
        # 检查 input_audio_config
        input_cfg = getattr(config, "input_audio_config", {})
        ros_device_idx = input_cfg.get("device_index")
        ros_device_name = input_cfg.get("device_name")
        
        print(f"\n  ROS/PyAudio 输入设备索引: {ros_device_idx}")
        print(f"  ROS/PyAudio 输入设备名称: {ros_device_name}")
        
    except ImportError:
        print("无法导入 config.py")


def main():
    print("=" * 60)
    print("双麦克风配置检查工具")
    print("=" * 60)
    
    # 1. 显示当前配置
    show_current_config()
    
    # 2. 列出设备
    input_devices = list_pyaudio_devices()
    list_alsa_devices()
    
    # 3. 交互测试
    print("\n" + "=" * 60)
    print("麦克风测试")
    print("=" * 60)
    
    if not input_devices:
        print("未检测到任何输入设备！")
        return
    
    while True:
        print("\n请选择要测试的麦克风索引（输入 q 退出）:")
        choice = input("> ").strip()
        
        if choice.lower() == 'q':
            break
        
        try:
            idx = int(choice)
            test_microphone(idx)
        except ValueError:
            print("请输入有效的数字索引")
    
    # 4. 配置建议
    print("\n" + "=" * 60)
    print("配置建议")
    print("=" * 60)
    print("""
【混合模式 (main_hybrid_mic.py) 配置】

1. 嘉宾麦克风配置（ROS 模式）：
   - 启动 audio_capture 时指定设备：
     roslaunch audio_capture capture.launch device:=hw:卡号,设备号
   
2. 辅助记者麦克风配置：
   - 在 config.py 中设置 ASSISTANT_MIC_INDEX = <索引>

【全 ASR 模式 (main_dual_mic.py) 配置】

1. 在 config.py 中设置：
   GUEST_MIC_INDEX = <嘉宾麦克风索引>
   ASSISTANT_MIC_INDEX = <辅助记者麦克风索引>

【运行命令】

# 混合模式（推荐，低延迟）
python main_hybrid_mic.py

# 全 ASR 模式
python main_dual_mic.py
""")


if __name__ == "__main__":
    main()
