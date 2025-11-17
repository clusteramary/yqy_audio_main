import wave

import pyaudio

DEVICE_INDEX = 2  # 设备索引
SAMPLE_RATE = 48000  # 采样率
CHANNELS = 1  # 单声道
FORMAT = pyaudio.paInt16
FRAMES_PER_BUFFER = 1024
RECORD_SECONDS = 4
OUTPUT_FILE = "test_record.wav"

# 初始化
p = pyaudio.PyAudio()

# 打开输入流
stream = p.open(
    format=FORMAT,
    channels=CHANNELS,
    rate=SAMPLE_RATE,
    input=True,
    input_device_index=DEVICE_INDEX,
    frames_per_buffer=FRAMES_PER_BUFFER,
)

print(f"🎤 开始录音 {RECORD_SECONDS} 秒...")
frames = []

# 录制循环
for _ in range(0, int(SAMPLE_RATE / FRAMES_PER_BUFFER * RECORD_SECONDS)):
    data = stream.read(FRAMES_PER_BUFFER)
    frames.append(data)

print("✅ 录音结束")

# 关闭流
stream.stop_stream()
stream.close()
p.terminate()

# 保存为 WAV 文件
wf = wave.open(OUTPUT_FILE, "wb")
wf.setnchannels(CHANNELS)
wf.setsampwidth(p.get_sample_size(FORMAT))
wf.setframerate(SAMPLE_RATE)
wf.writeframes(b"".join(frames))
wf.close()

print(f"💾 已保存到 {OUTPUT_FILE}")
