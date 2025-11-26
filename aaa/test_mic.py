import wave

import pyaudio

# 严格按照 config.py 中的 input_audio_config 参数
DEVICE_INDEX = 10  # 设备索引
SAMPLE_RATE = 48000  # 采样率
CHANNELS = 1  # 单声道
FORMAT = pyaudio.paInt16
CHUNK = 960  # 20ms @ 48k
RECORD_SECONDS = 4
OUTPUT_FILE = "test_record.wav"

# 初始化
p = pyaudio.PyAudio()

# 检查设备信息
try:
    device_info = p.get_device_info_by_index(DEVICE_INDEX)
    print(f"使用设备: {device_info['name']}")
    print(f"设备最大输入通道数: {device_info['maxInputChannels']}")
    print(f"设备默认采样率: {device_info['defaultSampleRate']}")
except Exception as e:
    print(f"无法获取设备信息: {e}")

# 打开输入流
try:
    stream = p.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        input_device_index=DEVICE_INDEX,
        frames_per_buffer=CHUNK,
    )
    print("✅ 音频流打开成功")
except Exception as e:
    print(f"❌ 无法打开音频流: {e}")
    p.terminate()
    exit(1)

print(f"🎤 开始录音 {RECORD_SECONDS} 秒...")
print(f"参数: {SAMPLE_RATE}Hz, {CHANNELS}声道, 16bit, 每块{CHUNK}样本")
frames = []

# 录制循环
try:
    total_chunks = int(SAMPLE_RATE / CHUNK * RECORD_SECONDS)
    for i in range(total_chunks):
        data = stream.read(CHUNK)
        frames.append(data)
        if (i + 1) % 50 == 0:  # 每50块打印一次进度
            seconds_recorded = (i + 1) * CHUNK / SAMPLE_RATE
            print(f"录制中... {seconds_recorded:.1f}秒")
    
    print("✅ 录音结束")
    
except Exception as e:
    print(f"❌ 录音过程中出错: {e}")

# 关闭流
stream.stop_stream()
stream.close()
p.terminate()

# 保存为 WAV 文件
try:
    wf = wave.open(OUTPUT_FILE, "wb")
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(FORMAT))
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(b"".join(frames))
    wf.close()
    print(f"💾 已保存到 {OUTPUT_FILE}")
    print(f"文件信息: {len(frames)}个音频块, 总时长{len(frames)*CHUNK/SAMPLE_RATE:.2f}秒")
except Exception as e:
    print(f"❌ 保存文件失败: {e}")