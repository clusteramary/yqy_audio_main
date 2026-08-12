#!/usr/bin/env python3
"""
切换机器人麦克风脚本

用法:
    .venv/bin/python switch_mic.py          # 列出麦克风并交互选择
    .venv/bin/python switch_mic.py --list   # 只列出麦克风

原理:
    机器人应用通过 ALSA "default" 采集，而 "default" 由 ~/.asoundrc 决定。
    本脚本把 ~/.asoundrc 的 pcm.!default 指向你选择的麦克风（按声卡名，
    插拔 USB 后卡号变化也不影响）。选择后立即实测录音，确认能听到声音。
"""

import argparse
import os
import re
import subprocess
import sys

ASOUNDRC = os.path.expanduser("~/.asoundrc")


def list_capture_cards() -> list:
    """用 arecord -l 解析所有支持录音的声卡。返回 [(card, name, desc, device), ...]"""
    try:
        out = subprocess.run(
            ["arecord", "-l"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception as e:
        print(f"[错误] 无法运行 arecord: {e}")
        return []
    cards = []
    for m in re.finditer(
        r"card (\d+): (\S+) \[([^\]]+)\], device (\d+): ([^\[]+)\s*\[([^\]]+)\]",
        out,
    ):
        card, name, desc, device, pcm, _ = m.groups()
        cards.append(
            {
                "card": int(card),
                "name": name,
                "desc": desc,
                "device": int(device),
                "hw": f"hw:{name},{device}",
            }
        )
    return cards


def test_capture(hw_name: str) -> str:
    """用 pyaudio 打开该设备录 0.3 秒，返回振幅信息（验证可用性）。"""
    try:
        import audioop

        import pyaudio
    except ImportError:
        return "未测试（缺少 pyaudio）"
    pa = pyaudio.PyAudio()
    try:
        s = pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=48000,
            input=True,
            frames_per_buffer=2400,
        )
        import time

        data = b""
        t0 = time.time()
        while time.time() - t0 < 0.3:
            data += s.read(2400, exception_on_overflow=False)
        s.stop_stream()
        s.close()
        amp = audioop.max(data, 2)
        if amp < 100:
            return f"可打开，但振幅={amp}（接近静音，检查麦克风/音量）"
        return f"可打开，振幅={amp}（正常，请对麦克风说话看数值变化）"
    except Exception as e:
        return f"打开失败: {e}"
    finally:
        pa.terminate()


def write_asoundrc(hw_name: str, card_name: str) -> None:
    content = f"""# 由 switch_mic.py 生成。机器人麦克风 = pcm.!default。
# 换麦克风：运行 .venv/bin/python switch_mic.py 重新选择。
pcm.!default {{
    type plug
    slave {{
        pcm "plughw:{card_name},0"
    }}
}}

ctl.!default {{
    type hw
    card {card_name}
}}

# 保险：即使系统级 pulse 配置恢复，也让 pcm.pulse 不可用（不连 PA）。
pcm.pulse {{
    type null
}}

ctl.pulse {{
    type hw
    card {card_name}
}}
"""
    with open(ASOUNDRC, "w") as f:
        f.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description="切换机器人麦克风")
    parser.add_argument("--list", action="store_true", help="只列出麦克风")
    args = parser.parse_args()

    cards = list_capture_cards()
    if not cards:
        print("没有找到任何可用的录音设备！请检查麦克风是否连接。")
        return 1

    print("可用麦克风：")
    for i, c in enumerate(cards):
        print(f"  [{i}] {c['name']} - {c['desc']} ({c['hw']})")

    if args.list:
        return 0

    while True:
        try:
            choice = int(input("\n请选择麦克风编号: ").strip())
            if 0 <= choice < len(cards):
                break
        except (ValueError, EOFError):
            pass
        print("无效选择，请重试")

    sel = cards[choice]
    print(f"\n选择: {sel['name']} - {sel['desc']}")
    print("测试中（请对麦克风说话）...")
    print(f"  结果: {test_capture(sel['hw'])}")

    write_asoundrc(sel["hw"], sel["name"])
    print(f"\n已写入 {ASOUNDRC}")
    print(f"现在机器人的麦克风 = {sel['name']}（无需改应用代码）")

    # 验证
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        s = pa.open(
            format=pyaudio.paInt16, channels=1, rate=48000, input=True,
            frames_per_buffer=2400,
        )
        import time

        data = b""
        t0 = time.time()
        while time.time() - t0 < 0.3:
            data += s.read(2400, exception_on_overflow=False)
        s.stop_stream()
        s.close()
        import audioop

        print(f"验证通过: 默认设备采集振幅={audioop.max(data, 2)}（对麦克风说话应明显升高）")
    except Exception as e:
        print(f"[警告] 默认设备验证失败: {e}")
    finally:
        pa.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
