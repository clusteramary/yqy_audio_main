import pyaudio


def list_audio_devices():
    p = pyaudio.PyAudio()
    print("====== 输入/输出音频设备列表 ======")
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        name = dev.get("name")
        host_api = p.get_host_api_info_by_index(dev["hostApi"]).get("name")
        max_input = dev.get("maxInputChannels")
        max_output = dev.get("maxOutputChannels")
        print(
            f"[{i}] {name} | Host API: {host_api} | 输入通道: {max_input} | 输出通道: {max_output}"
        )
    p.terminate()


if __name__ == "__main__":
    list_audio_devices()
