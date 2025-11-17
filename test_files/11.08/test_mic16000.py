import pyaudio

DEVICE_INDEX = 1  # 你当前用的索引
SAMPLE_RATE = 44100  # 你要用的采样率
CHANNELS = 1

p = pyaudio.PyAudio()
dev = p.get_device_info_by_index(DEVICE_INDEX)
print("设备信息：", dev)
print("默认采样率：", dev.get("defaultSampleRate"))

try:
    stream = p.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        input_device_index=DEVICE_INDEX,
        frames_per_buffer=320,
    )
    print("✅ 能以 16k 打开该设备！")
    stream.close()
except Exception as e:
    print("❌ 以 16k 打开失败：", e)
finally:
    p.terminate()
