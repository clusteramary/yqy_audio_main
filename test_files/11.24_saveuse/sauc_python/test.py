import os
import threading
import time

import pyaudio


def listen_audio(duration=5):
    """简单测试：连续从麦克风读 duration 秒"""
    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=16000,
        input=True,
        frames_per_buffer=1024,
    )
    t_end = time.time() + duration
    print("[audio] start capture")
    while time.time() < t_end:
        # 只是读一下，不做处理
        stream.read(1024, exception_on_overflow=False)
    stream.stop_stream()
    stream.close()
    p.terminate()
    print("[audio] Audio capture finished")


def watch_ctrl(path="ctrl.txt"):
    """监听 ctrl.txt 修改时间，文件不存在时等待创建，而不是直接崩溃"""
    # 1）如果一开始没有 ctrl.txt，就等它被创建
    while not os.path.exists(path):
        print(f"[ctrl] {path} not found, waiting...")
        time.sleep(0.5)

    last_mod = os.path.getmtime(path)
    print(f"[ctrl] start watching {path}, initial mtime={last_mod}")

    while True:
        time.sleep(0.1)
        try:
            mod = os.path.getmtime(path)
        except FileNotFoundError:
            # 被删了就重新等
            print(f"[ctrl] {path} deleted, waiting recreate...")
            while not os.path.exists(path):
                time.sleep(0.5)
            last_mod = os.path.getmtime(path)
            print(f"[ctrl] {path} recreated, new mtime={last_mod}")
            continue

        if mod != last_mod:
            print(f"[ctrl] Control signal detected! old={last_mod}, new={mod}")
            last_mod = mod


if __name__ == "__main__":
    # 先确保有一个空的 ctrl.txt，避免一开机就 FileNotFoundError
    if not os.path.exists("ctrl.txt"):
        open("ctrl.txt", "w", encoding="utf-8").close()
        print("[main] created empty ctrl.txt")

    t_audio = threading.Thread(
        target=listen_audio, kwargs={"duration": 10}, daemon=True
    )
    t_ctrl = threading.Thread(target=watch_ctrl, daemon=True)

    t_audio.start()
    t_ctrl.start()

    # 主线程等音频线程结束
    t_audio.join()
    print("[main] audio thread finished, ctrl watcher is still running (daemon).")
    print(
        "[main] 可以在 10 秒内多次编辑 ctrl.txt，看打印是否即时出现 [ctrl] Control signal detected。"
    )
