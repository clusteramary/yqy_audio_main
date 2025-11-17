# test_mic.py
import time
import wave

import pyaudio


def test_microphone(device_index=10):
    pa = pyaudio.PyAudio()
    
    # 录音参数
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 48000
    CHUNK = 960
    RECORD_SECONDS = 3
    WAVE_OUTPUT_FILENAME = "test_mic.wav"
    
    print(f"测试设备 #{device_index}...")
    
    try:
        stream = pa.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        input_device_index=device_index,
                        frames_per_buffer=CHUNK)
        
        print("开始录音3秒...")
        frames = []
        
        for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
            data = stream.read(CHUNK)
            frames.append(data)
            if i % 50 == 0:  # 每50帧打印一次
                print(f"录制中... {len(frames)} 帧")
        
        stream.stop_stream()
        stream.close()
        
        # 保存为WAV文件
        wf = wave.open(WAVE_OUTPUT_FILENAME, 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(pa.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        wf.close()
        
        print(f"录音已保存为 {WAVE_OUTPUT_FILENAME}")
        
    except Exception as e:
        print(f"设备 #{device_index} 错误: {e}")
    finally:
        pa.terminate()

if __name__ == "__main__":
    # 测试设备0,1,2
    for i in range(1):
        test_microphone()
        time.sleep(1)