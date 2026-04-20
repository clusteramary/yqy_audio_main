#!/usr/bin/env python3
"""
音频设备检测与测试工具。

用法：
  python detect_audio_devices.py                 # 列出所有音频设备
  python detect_audio_devices.py --suitable       # 仅显示适合输入/输出的设备
  python detect_audio_devices.py --test 3         # 测试设备 3（录音 3 秒）
  python detect_audio_devices.py --test 3 -d 5    # 测试设备 3，录音 5 秒
"""

import argparse
import sys
import time
import wave

import pyaudio

CHUNK = 1024
DEFAULT_DURATION = 3


def list_all_devices(pa: pyaudio.PyAudio, show_suitable_only: bool = False):
    """列出所有音频设备信息。"""
    try:
        default_input = pa.get_default_input_device_info()["index"]
    except Exception:
        default_input = -1
    try:
        default_output = pa.get_default_output_device_info()["index"]
    except Exception:
        default_output = -1

    device_count = pa.get_device_count()
    input_devices = []
    output_devices = []

    for i in range(device_count):
        try:
            info = pa.get_device_info_by_index(i)
        except Exception:
            continue

        name = info.get("name", "Unknown")
        host_api = pa.get_host_api_info_by_index(info["hostApi"]).get("name", "?")
        max_input = info.get("maxInputChannels", 0)
        max_output = info.get("maxOutputChannels", 0)
        default_sr = info.get("defaultSampleRate", 0)

        is_default_in = " <-- 默认输入" if i == default_input else ""
        is_default_out = " <-- 默认输出" if i == default_output else ""

        if show_suitable_only:
            if max_input > 0:
                input_devices.append((i, name, host_api, max_input, default_sr, is_default_in))
            if max_output > 0:
                output_devices.append((i, name, host_api, max_output, default_sr, is_default_out))
        else:
            flags = []
            if max_input > 0:
                flags.append(f"输入x{max_input}")
            if max_output > 0:
                flags.append(f"输出x{max_output}")
            flag_str = " | ".join(flags) if flags else "无"

            default_mark = is_default_in or is_default_out
            print(f"  [{i:2d}] {name}")
            print(f"       Host API: {host_api} | {flag_str} | 默认采样率: {default_sr:.0f}Hz{default_mark}")

    if show_suitable_only:
        print("\n========== 适合作为输入（麦克风） ==========")
        if input_devices:
            for idx, name, host_api, ch, sr, mark in input_devices:
                print(f"  [{idx:2d}] {name} ({host_api}, {ch}ch, {sr:.0f}Hz){mark}")
        else:
            print("  （无）")

        print("\n========== 适合作为输出（扬声器） ==========")
        if output_devices:
            for idx, name, host_api, ch, sr, mark in output_devices:
                print(f"  [{idx:2d}] {name} ({host_api}, {ch}ch, {sr:.0f}Hz){mark}")
        else:
            print("  （无）")


def test_device(pa: pyaudio.PyAudio, index: int, duration: int):
    """测试指定设备：录音并保存为 WAV。"""
    device_count = pa.get_device_count()
    if index < 0 or index >= device_count:
        print(f"错误：设备索引 {index} 超出范围（0 ~ {device_count - 1}）")
        sys.exit(1)

    try:
        info = pa.get_device_info_by_index(index)
    except Exception as e:
        print(f"错误：无法获取设备 {index} 的信息: {e}")
        sys.exit(1)

    name = info.get("name", "Unknown")
    max_input = info.get("maxInputChannels", 0)
    default_sr = info.get("defaultSampleRate", 48000)

    if max_input == 0:
        print(f"错误：设备 [{index}] \"{name}\" 没有输入通道，无法用于录音")
        print("请使用 --suitable 查看适合录音的设备")
        sys.exit(1)

    channels = min(max_input, 1)  # 使用单声道
    sample_rate = int(default_sr)

    print(f"测试设备 [{index}] \"{name}\"")
    print(f"  采样率: {sample_rate}Hz | 声道: {channels} | 时长: {duration}s")

    # 尝试打开流
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            input_device_index=index,
            frames_per_buffer=CHUNK,
        )
    except Exception as e:
        print(f"错误：无法打开设备 [{index}]: {e}")
        # 尝试降低采样率
        for fallback_sr in [48000, 44100, 16000]:
            if fallback_sr == sample_rate:
                continue
            print(f"  尝试降采样率到 {fallback_sr}Hz...")
            try:
                sample_rate = fallback_sr
                stream = pa.open(
                    format=pyaudio.paInt16,
                    channels=channels,
                    rate=sample_rate,
                    input=True,
                    input_device_index=index,
                    frames_per_buffer=CHUNK,
                )
                print(f"  成功！使用 {sample_rate}Hz")
                break
            except Exception:
                continue
        else:
            print("所有采样率均失败，请检查设备")
            sys.exit(1)

    print(f"开始录音 {duration} 秒...")
    frames = []
    total_chunks = int(sample_rate / CHUNK * duration)

    try:
        for i in range(total_chunks):
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)
            if (i + 1) % max(1, (total_chunks // 5)) == 0:
                elapsed = (i + 1) * CHUNK / sample_rate
                print(f"  录音中... {elapsed:.1f}s / {duration}s")
    except Exception as e:
        print(f"录音出错: {e}")
    finally:
        stream.stop_stream()
        stream.close()

    # 保存 WAV
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"test_device_{index}_{timestamp}.wav"
    try:
        with wave.open(filename, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # paInt16 = 2 bytes
            wf.setframerate(sample_rate)
            wf.writeframes(b"".join(frames))
        duration_actual = len(frames) * CHUNK / sample_rate
        print(f"已保存到 {filename}（{duration_actual:.2f}s, {len(frames)} 帧）")
    except Exception as e:
        print(f"保存失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="音频设备检测与测试工具")
    parser.add_argument(
        "--test", type=int, metavar="INDEX", help="测试指定索引的录音设备"
    )
    parser.add_argument(
        "-d", "--duration", type=int, default=DEFAULT_DURATION,
        help=f"录音时长（秒），默认 {DEFAULT_DURATION}s（仅 --test 模式）",
    )
    parser.add_argument(
        "--suitable", action="store_true",
        help="仅显示适合输入/输出的设备",
    )
    args = parser.parse_args()

    pa = pyaudio.PyAudio()

    try:
        if args.test is not None:
            test_device(pa, args.test, args.duration)
        else:
            header = "========== 适合输入/输出的设备 ==========" if args.suitable else "========== 所有音频设备 =========="
            print(header)
            list_all_devices(pa, show_suitable_only=args.suitable)
    finally:
        pa.terminate()


if __name__ == "__main__":
    main()
