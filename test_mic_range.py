#!/usr/bin/env python3
"""
麦克风收音范围测试工具

用于测试和调优辅助记者麦克风的收音范围参数，
确保能识别辅助记者的声音，但不误识别嘉宾的声音。

使用方法：
1. python test_mic_range.py --device 2  # 测试设备索引 2
2. 对着麦克风说话，观察是否被识别
3. 让别人在远处说话，观察是否被过滤掉
4. 根据结果调整 config.py 中的参数
"""

import argparse
import audioop
import os
import sys
import time

os.environ.setdefault('PA_ALSA_PLUGHW', '1')
os.environ.setdefault('JACK_NO_AUDIO_RESERVATION', '1')

import pyaudio


class MicRangeTester:
    """麦克风收音范围测试器"""
    
    def __init__(
        self,
        device_index: int,
        vad_threshold: int = 800,
        min_speaking_duration_ms: int = 400,
        volume_stability_check: bool = True,
        volume_variance_threshold: float = 0.3,
    ):
        self.device_index = device_index
        self.vad_threshold = vad_threshold
        self.min_speaking_duration_ms = min_speaking_duration_ms
        self.volume_stability_check = volume_stability_check
        self.volume_variance_threshold = volume_variance_threshold
        
        # 音频参数
        self.sample_rate = 16000
        self.chunk_ms = 100
        self.chunk_samples = int(self.sample_rate * self.chunk_ms / 1000)
        
        self.pa = None
        self.stream = None
        
    def start_test(self):
        """开始测试"""
        print("\n" + "=" * 60)
        print("麦克风收音范围测试")
        print("=" * 60)
        print(f"设备索引: {self.device_index}")
        print(f"VAD 阈值: {self.vad_threshold}")
        print(f"最小说话时长: {self.min_speaking_duration_ms}ms")
        print(f"音量稳定性检测: {'启用' if self.volume_stability_check else '禁用'}")
        print(f"音量方差阈值: {self.volume_variance_threshold}")
        print("=" * 60)
        
        try:
            self.pa = pyaudio.PyAudio()
            
            # 打开麦克风
            self.stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                input_device_index=self.device_index,
                frames_per_buffer=self.chunk_samples,
            )
            
            print("\n正在监听麦克风...")
            print("请对着麦克风说话（近距离），观察是否被接受")
            print("然后让别人在远处说话，观察是否被过滤")
            print("\n按 Ctrl+C 停止\n")
            
            # 持续监听
            frames = []
            rms_history = []
            speaking = False
            silence_ms = 0
            speaking_duration_ms = 0
            vad_silence_ms = 600
            
            while True:
                data = self.stream.read(self.chunk_samples, exception_on_overflow=False)
                rms = audioop.rms(data, 2)
                
                # 实时显示音量
                bar_len = min(int(rms / 50), 60)
                bar = "█" * bar_len
                threshold_pos = int(self.vad_threshold / 50)
                threshold_mark = " " * threshold_pos + "┃"
                
                status = ""
                if rms > self.vad_threshold:
                    status = "🎤 语音"
                    frames.append(data)
                    rms_history.append(float(rms))
                    speaking = True
                    silence_ms = 0
                    speaking_duration_ms += self.chunk_ms
                elif speaking:
                    frames.append(data)
                    rms_history.append(float(rms))
                    silence_ms += self.chunk_ms
                    status = "⏸ 静音中..."
                    
                    if silence_ms >= vad_silence_ms:
                        # 一句话结束，进行检查
                        result = self._check_recording(frames, rms_history, speaking_duration_ms)
                        print(f"\n{result}\n")
                        
                        # 重置
                        frames = []
                        rms_history = []
                        speaking = False
                        silence_ms = 0
                        speaking_duration_ms = 0
                
                print(f"\r  {threshold_mark}", end="")
                print(f"\r  音量: [{bar:<60}] RMS: {rms:5.0f}  {status}  ", end="", flush=True)
                
        except KeyboardInterrupt:
            print("\n\n测试结束")
        except Exception as e:
            print(f"\n错误: {e}")
        finally:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            if self.pa:
                self.pa.terminate()
    
    def _check_recording(self, frames, rms_history, speaking_duration_ms):
        """检查录音是否应该接受"""
        if not frames:
            return "⚠ 未检测到有效语音"
        
        results = []
        
        # 1. 检查最小说话时长
        if speaking_duration_ms < self.min_speaking_duration_ms:
            results.append(
                f"❌ 说话时长 {speaking_duration_ms}ms < 最小 {self.min_speaking_duration_ms}ms → 拒绝（可能是远距离）"
            )
            return "\n  ".join(results)
        else:
            results.append(f"✓ 说话时长 {speaking_duration_ms}ms ≥ 最小 {self.min_speaking_duration_ms}ms")
        
        # 2. 检查音量稳定性
        if self.volume_stability_check and len(rms_history) > 3:
            valid_rms = [r for r in rms_history if r > self.vad_threshold * 1.2]
            
            if len(valid_rms) >= 3:
                mean_rms = sum(valid_rms) / len(valid_rms)
                variance = sum((r - mean_rms) ** 2 for r in valid_rms) / len(valid_rms)
                std_dev = variance ** 0.5
                cv = std_dev / mean_rms if mean_rms > 0 else 1.0
                
                if cv > self.volume_variance_threshold:
                    results.append(
                        f"❌ 音量不稳定 CV={cv:.2f} > {self.volume_variance_threshold} → 拒绝（可能是远距离）"
                    )
                else:
                    results.append(f"✓ 音量稳定 CV={cv:.2f} ≤ {self.volume_variance_threshold}")
            else:
                results.append("⚠ 有效样本不足，跳过稳定性检测")
        
        # 判断最终结果
        if any("❌" in r for r in results):
            results.insert(0, "🚫 录音被拒绝")
        else:
            results.insert(0, "✅ 录音被接受（会进行 ASR 识别）")
        
        return "\n  ".join(results)


def main():
    parser = argparse.ArgumentParser(description="麦克风收音范围测试工具")
    parser.add_argument("--device", type=int, required=True, help="麦克风设备索引")
    parser.add_argument("--threshold", type=int, default=800, help="VAD 阈值（默认 800）")
    parser.add_argument("--min-duration", type=int, default=400, help="最小说话时长 ms（默认 400）")
    parser.add_argument("--no-stability", action="store_true", help="禁用音量稳定性检测")
    parser.add_argument("--variance", type=float, default=0.3, help="音量方差阈值（默认 0.3）")
    
    args = parser.parse_args()
    
    tester = MicRangeTester(
        device_index=args.device,
        vad_threshold=args.threshold,
        min_speaking_duration_ms=args.min_duration,
        volume_stability_check=not args.no_stability,
        volume_variance_threshold=args.variance,
    )
    
    tester.start_test()


if __name__ == "__main__":
    main()
