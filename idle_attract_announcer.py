#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
idle_attract_announcer.py —— 闲时招揽客户语音脚本

功能：
    机器人空闲时，每固定间隔（默认 15 秒）通过 ROS 话题 /audio 说一句固定的招揽语，
    例如："你好，有空来做个小小的机器人访谈嘛？"

重要说明：
    1. 本脚本独立运行，请不要与 main.py 同时运行（两者都会往 /audio 发布语音）。
    2. 语音合成（TTS）与 main.py 完全一致：同样请求 format="pcm"（服务端实际返回
       float32 / 24kHz / 单声道 PCM，下位机按 float32 播放——这也是 main.py 正常的原因）。
       【注意】不要改成 "pcm_s16le"：下位机按 float32 解析，s16le 字节会播成噪声（乱码）。
       发布节奏按 float32 实时码率 96000 字节/秒进行，保证语速正常。
       不发送 say_hello 附带的 UDP "start" 关键词，避免触发其他控制逻辑。
    3. 语音发布与 main.py 完全相同的 ROS1 发布链路：
           config.output_audio_config
           -> AudioDeviceManager.open_output_stream()
           -> Ros1SpeakerStream.write() 发布到话题 /audio
           -> 下位机 ros_audio_player.py 订阅 /audio 播放
    4. 本脚本为新增文件，不修改任何现有代码。

运行方式（ROS 扬声器模式）：
    source /opt/ros/noetic/setup.bash
    cd /home/nvidia/Documents/Robot-Voice/yqy_audio_main
    OUTPUT_AUDIO_MODE=ros1 .venv/bin/python idle_attract_announcer.py

停止：按 Ctrl + C
"""

import argparse
import asyncio
import gzip
import hashlib
import json
import time
import uuid
from pathlib import Path

import config
import protocol
from audio_constants import AudioConfig
from audio_device_manager import AudioDeviceManager
from realtime_dialog_client import RealtimeDialogClient

# ============================ 可配置项（改这里即可） ============================
# 固定输出的招揽语句内容
ATTRACT_PHRASE = "你好，有空来做个小小的机器人访谈嘛？"

# 每隔多少秒说一次（固定间隔，从每次开始说的时间起算）
ANNOUNCE_INTERVAL_SEC = 8.0

# 每块音频发布时长（毫秒）：按真实时间节奏发布到 ROS，与 main.py 流式播放一致
PUBLISH_CHUNK_MS = 100

# TTS 返回音频的真实格式（与 main.py 的 "pcm" 请求一致）：
# 服务端实际返回 float32 / 24kHz / 单声道（下位机按 float32 播放）
# 实时码率 = 24000 * 4 = 96000 字节/秒（之前按 16bit 的 48000 字节/秒发布，所以慢一倍）
TTS_OUTPUT_SAMPLE_RATE = 24000
TTS_OUTPUT_BYTES_PER_SAMPLE = 4  # float32

# 合成语音的本地缓存目录（放在 logs/ 下避免污染 git；网络异常时可直接复用缓存）
TTS_CACHE_DIR = Path(__file__).resolve().parent / "logs"
# ==============================================================================


# ----------------------------------------------------------------------
# TTS 缓存：同一句话只合成一次，后续直接从本地 PCM 缓存读取
# ----------------------------------------------------------------------
def _cache_path(phrase: str) -> Path:
    # f32le 表示缓存的是 format="pcm" 返回的 float32 PCM（与 main.py 相同；
    # 旧 s16le 缓存会被下位机按 float32 播成噪声，命名区分避免误用）
    digest = hashlib.md5(phrase.encode("utf-8")).hexdigest()[:8]
    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return TTS_CACHE_DIR / f"idle_attract_f32le_{digest}.pcm"


def load_cached_audio(phrase: str) -> bytes:
    path = _cache_path(phrase)
    if not path.exists():
        return b""
    try:
        data = path.read_bytes()
    except Exception as e:
        print(f"[TTS] 读取缓存失败: {e}")
        return b""
    if data:
        # float32 * 24kHz * 单声道 = 96000 字节/秒
        print(
            f"[TTS] 从缓存加载语音 {path.name}"
            f"（{len(data)} 字节，约 {len(data) / 96000.0:.1f}s）"
        )
    return data


def save_cached_audio(phrase: str, data: bytes) -> None:
    path = _cache_path(phrase)
    try:
        path.write_bytes(data)
        print(f"[TTS] 语音已缓存到 {path.name}")
    except Exception as e:
        print(f"[TTS] 缓存写入失败: {e}")


# ----------------------------------------------------------------------
# TTS 合成：与 main.py 开场白相同的火山引擎 TTS 通道（say_hello / 事件 300）
# 收集服务器返回的 PCM 音频（float32/24kHz/单声道，与 main.py 一致）
# ----------------------------------------------------------------------
async def synthesize_phrase(phrase: str) -> bytes:
    """把一句文本合成为 float32/24kHz/单声道 PCM，返回原始音频字节（与 main.py 相同）。

    复用 main.py 开场白(say_hello)的事件 300 通道：它在会话刚建立时就能直接
    返回 TTS 音频；而 chat_tts_text(500) 在全新会话中不会返回音频
    （main.py 里只在已有对话的中途使用它）。
    """
    client = RealtimeDialogClient(
        config=config.ws_connect_config,
        session_id=str(uuid.uuid4()),
        # 与 main.py 相同：请求 "pcm"。服务端实际返回 float32/24kHz/单声道，
        # 下位机按 float32 播放（main.py 正常的原因）。
        # 不要改 "pcm_s16le"：下位机会把 s16le 字节按 float32 解析，播成噪声。
        output_audio_format="pcm",
    )
    chunks = []
    got_audio = False

    try:
        await client.connect()

        # 与 RealtimeDialogClient.say_hello() 相同的 300 消息，但不发送其附带的
        # UDP "start" 关键词，避免招揽脚本触发其他控制逻辑。
        payload = {"content": phrase}
        hello_request = bytearray(protocol.generate_header())
        hello_request.extend(int(300).to_bytes(4, "big"))
        payload_bytes = gzip.compress(str.encode(json.dumps(payload)))
        hello_request.extend((len(client.session_id)).to_bytes(4, "big"))
        hello_request.extend(str.encode(client.session_id))
        hello_request.extend((len(payload_bytes)).to_bytes(4, "big"))
        hello_request.extend(payload_bytes)
        await client.ws.send(hello_request)
        print(f"[TTS] 已发送 TTS 请求(事件300): {phrase!r}")

        while True:
            try:
                response = await asyncio.wait_for(
                    client.receive_server_response(), timeout=5.0
                )
            except asyncio.TimeoutError:
                # 音频流结束后服务器不再回包，超时且已收到音频即可收尾
                if got_audio:
                    break
                raise RuntimeError("等待 TTS 音频超时")

            if not isinstance(response, dict):
                continue

            msg_type = response.get("message_type")

            # SERVER_ACK 中的 bytes 就是 TTS 音频块
            if msg_type == "SERVER_ACK":
                payload = response.get("payload_msg")
                if isinstance(payload, bytes):
                    if payload:
                        chunks.append(payload)
                        got_audio = True
                continue

            if msg_type == "SERVER_FULL_RESPONSE":
                event = response.get("event")
                # 359 = 本段 TTS 播放结束；152/153 = 会话结束
                if event in (359, 152, 153):
                    break
                continue

            if msg_type == "SERVER_ERROR":
                raise RuntimeError(f"TTS 服务器错误: {response.get('payload_msg')}")
    finally:
        try:
            await client.finish_session()
        except Exception:
            pass
        await client.close()

    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("TTS 未返回任何音频数据")
    return audio


# ----------------------------------------------------------------------
# 语音发布：与 main.py 相同的 ROS1 发布链路
# ----------------------------------------------------------------------
async def publish_audio(output_stream, audio: bytes, duplex_mode: str) -> None:
    """按真实时间节奏把 PCM 发布到 /audio（或本地扬声器）。"""
    if not audio:
        return

    sample_rate = TTS_OUTPUT_SAMPLE_RATE
    bytes_per_sample = TTS_OUTPUT_BYTES_PER_SAMPLE  # 4 = float32
    chunk_bytes = max(
        bytes_per_sample, sample_rate * bytes_per_sample * PUBLISH_CHUNK_MS // 1000
    )  # 9600 字节/100ms = 96000 字节/秒（float32 实时码率，语速正常）

    # 全双工模式下给每句话一个 utterance_id，便于下位机打断；
    # 与 main.py 的 _write_framed_to_ros 保持一致（每块都标记 is_last=True）。
    utterance_id = int(time.time() * 1000) & 0x7FFFFFFF
    seq = 0
    offset = 0
    total = len(audio)

    while offset < total:
        piece = audio[offset : offset + chunk_bytes]
        offset += chunk_bytes
        if duplex_mode == "full":
            output_stream.write_framed(utterance_id, seq, True, piece)
        else:
            output_stream.write(piece)
        seq += 1
        await asyncio.sleep(PUBLISH_CHUNK_MS / 1000.0)


# ----------------------------------------------------------------------
# 主循环：固定间隔播报
# ----------------------------------------------------------------------
async def announce_loop(output_stream, duplex_mode: str, phrase: str, interval_sec: float) -> None:
    audio = load_cached_audio(phrase)
    count = 0
    print(f"[ANN] 招揽脚本已启动：每 {interval_sec:.1f}s 说一次：{phrase!r}")

    while True:
        # 无缓存时先合成（失败则等下一个周期重试，不影响后续循环）
        if not audio:
            print("[TTS] 开始合成招揽语音...")
            try:
                audio = await synthesize_phrase(phrase)
                save_cached_audio(phrase, audio)
            except Exception as e:
                print(f"[TTS] 合成失败: {e}；{interval_sec:.1f}s 后重试")
                await asyncio.sleep(interval_sec)
                continue

        cycle_start = time.monotonic()
        count += 1
        print(f"[ANN #{count}] 播放：{phrase}")
        try:
            await publish_audio(output_stream, audio, duplex_mode)
        except Exception as e:
            print(f"[ANN] 发布语音失败: {e}")

        # 固定间隔：从本次开始播放的时刻起算，保证"每 interval_sec 秒说一句"
        elapsed = time.monotonic() - cycle_start
        if elapsed < interval_sec:
            await asyncio.sleep(interval_sec - elapsed)


async def async_main(args) -> None:
    config.DUPLEX_MODE = args.duplex_mode
    config.OUTPUT_AUDIO_MODE = args.output_audio_mode
    config.output_audio_config["mode"] = args.output_audio_mode
    config.output_audio_config["duplex_mode"] = args.duplex_mode

    phrase = args.phrase
    interval = args.interval

    print("=" * 60)
    print("[ANN] 闲时招揽客户语音脚本启动")
    print(
        f"[ANN] 输出模式: {config.OUTPUT_AUDIO_MODE}"
        f"（ROS 话题 {config.output_audio_config['ros1_topic']}）"
    )
    print(f"[ANN] 双工模式: {config.DUPLEX_MODE}")
    print(f"[ANN] 播报间隔: {interval}s，语句: {phrase}")
    print("=" * 60)

    # 与 main.py / dialog_session.py 完全相同的输出流创建方式
    audio_device = AudioDeviceManager(
        AudioConfig(**config.get_active_input_config()),
        AudioConfig(**config.output_audio_config),
    )
    try:
        output_stream = audio_device.open_output_stream()
        print("[ANN] 输出流已打开。")
    except Exception as e:
        print(f"[ANN] 打开输出失败: {e}")
        audio_device.cleanup()
        raise

    try:
        await announce_loop(output_stream, config.DUPLEX_MODE, phrase, interval)
    finally:
        audio_device.cleanup()
        print("[ANN] 已退出，输出设备已释放。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="闲时招揽客户语音脚本（独立于 main.py 运行，请勿与 main.py 同时启动）"
    )
    parser.add_argument(
        "--phrase",
        type=str,
        default=ATTRACT_PHRASE,
        help=f"要说的固定语句（默认：{ATTRACT_PHRASE}）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=ANNOUNCE_INTERVAL_SEC,
        help=f"每隔多少秒说一次（默认：{ANNOUNCE_INTERVAL_SEC}）",
    )
    parser.add_argument(
        "--duplex-mode",
        choices=("half", "full"),
        default=config.DUPLEX_MODE,
        help="双工模式：half=半双工（默认），full=全双工可打断",
    )
    parser.add_argument(
        "--output-audio-mode",
        choices=("pyaudio", "ros1"),
        default=config.OUTPUT_AUDIO_MODE,
        help="扬声器输出：pyaudio=本地扬声器，ros1=下位机 ros_audio_player.py",
    )
    args = parser.parse_args()

    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("[ANN] 程序被用户中断")
