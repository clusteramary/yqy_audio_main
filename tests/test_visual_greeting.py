# -*- coding: utf-8 -*-
"""visual_greeting 迎宾状态机的逻辑仿真测试（不依赖 ROS/相机/网络）。

mock 掉 detector 与 session，验证：
  A) 程序开始时（未说结束语、用户有输入）不开启迎宾
  B) 结束语说完 + TTS 播完 → 开启迎宾
  C) 麦克风静默超过阈值 → 开启迎宾
  D) 检测到人脸 → 发欢迎语(500) → 等播完 → stop_event 置位（会话重启）
  E) 500 失败时退回 501
  F) 视觉检测异常不影响会话（不置位 stop_event、不抛异常）
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config

config.ENABLE_VISUAL_GREETING = True
config.VISUAL_GREETING_MIN_SESSION_SEC = 1.0   # 测试加速
config.VISUAL_GREETING_SILENCE_SEC = 2.0       # 测试加速
config.VISUAL_GREETING_TEXT = "欢迎测试语"

import main  # noqa: E402  (visual_greeting 逻辑所在)


class FakeClient:
    def __init__(self):
        self.tts_calls = []
        self.chat_calls = []
        self.tts_fail = False

    async def chat_tts_text(self, is_user_querying, start, end, content):
        if self.tts_fail:
            raise RuntimeError("tts down")
        self.tts_calls.append((start, end, content))

    async def chat_text_query(self, content):
        self.chat_calls.append(content)


class FakeDetector:
    def __init__(self, result=True, delay=0.0):
        self.result = result
        self.delay = delay

    def wait_for_stable_face(self, interval_sec, required_consecutive,
                             min_face_width, stop_event):
        time.sleep(self.delay)
        return self.result


class FakeSession:
    def __init__(self):
        self.client = FakeClient()
        self._ending_said = False
        self._tts_playing = False
        self.is_user_querying = False
        self._model_replying = False
        self.last_user_activity_ts = time.time()

    def is_ending_said(self):
        return self._ending_said

    def idle_silence_sec(self):
        return time.time() - self.last_user_activity_ts

    def _is_tts_playing(self):
        return self._tts_playing


def _run(coro_factory, timeout=15.0):
    async def _inner():
        stop_event = asyncio.Event()
        session = FakeSession()
        coro = coro_factory(session, stop_event)
        task = asyncio.create_task(coro)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        return session, stop_event

    return asyncio.run(_inner())


def test_A_startup_no_greeting_without_trigger():
    """程序开始时（结束语未说、用户一直有输入）不开启迎宾：stop_event 不被置位。"""
    def factory(session, stop_event):
        # 用户持续有输入：后台每 0.3s 刷新活动时间戳
        async def keep_alive():
            while not stop_event.is_set():
                session.last_user_activity_ts = time.time()
                await asyncio.sleep(0.3)
        async def wrapper():
            await asyncio.gather(
                keep_alive(),
                main.visual_greeting(FakeDetector(result=True, delay=0.2),
                                     session, stop_event),
            )
        return wrapper()

    session, stop_event = _run(factory, timeout=4.0)
    assert not stop_event.is_set(), "用户在说话时不应触发迎宾/重启"
    assert session.client.tts_calls == [] and session.client.chat_calls == []
    print("A PASS: 程序开始（无结束语/持续有输入）不迎宾")


def test_B_ending_said_triggers_greeting():
    """结束语说完 + TTS 播完 → 迎宾开启 → 检测到脸 → 欢迎语 → 重启。"""
    def factory(session, stop_event):
        async def wrapper():
            # 用户活动保持新鲜（排除静默路径），会话 1s 后报告结束语已说
            async def fresh():
                while not stop_event.is_set():
                    session.last_user_activity_ts = time.time()
                    await asyncio.sleep(0.3)
            async def say_ending():
                await asyncio.sleep(1.2)  # 超过 MIN_SESSION_SEC
                session._ending_said = True
            await asyncio.gather(
                fresh(), say_ending(),
                main.visual_greeting(FakeDetector(result=True, delay=0.2),
                                     session, stop_event),
            )
        return wrapper()

    session, stop_event = _run(factory, timeout=10.0)
    assert stop_event.is_set(), "结束语说完+检测到脸后应触发会话重启"
    assert session.client.tts_calls, "应通过 500 发出欢迎语"
    _, _, content = session.client.tts_calls[0]
    assert content == config.VISUAL_GREETING_TEXT
    print("B PASS: 结束语说完 → 迎宾 → 500 欢迎语 → 重启会话")


def test_C_silence_triggers_greeting():
    """麦克风静默超过阈值 → 迎宾开启（无结束语）。"""
    def factory(session, stop_event):
        async def wrapper():
            await main.visual_greeting(FakeDetector(result=True, delay=0.2),
                                       session, stop_event)
        return wrapper()

    session, stop_event = _run(factory, timeout=10.0)
    assert stop_event.is_set(), "静默超时+检测到脸后应触发会话重启"
    assert session.client.tts_calls
    print("C PASS: 麦克风静默超时 → 迎宾 → 欢迎语 → 重启会话")


def test_D_no_face_no_restart():
    """开启迎宾后一直没有人脸 → 不重启、不发欢迎语。"""
    def factory(session, stop_event):
        async def wrapper():
            await main.visual_greeting(FakeDetector(result=False, delay=0.2),
                                       session, stop_event)
        return wrapper()

    session, stop_event = _run(factory, timeout=6.0)
    assert not stop_event.is_set()
    assert session.client.tts_calls == []
    print("D PASS: 无人脸不触发欢迎/重启")


def test_E_tts_fail_fallback_to_501():
    """500 失败 → 退回 501。"""
    def factory(session, stop_event):
        session.client.tts_fail = True
        async def wrapper():
            await main.visual_greeting(FakeDetector(result=True, delay=0.2),
                                       session, stop_event)
        return wrapper()

    session, stop_event = _run(factory, timeout=10.0)
    assert stop_event.is_set()
    assert session.client.chat_calls, "500 失败应退回 501"
    assert config.VISUAL_GREETING_TEXT in session.client.chat_calls[0]
    print("E PASS: 500 失败退回 501 并重启")


def test_F_detector_exception_isolated():
    """视觉检测抛异常 → 迎宾任务静默结束，不影响会话（stop_event 不置位）。"""
    class BoomDetector(FakeDetector):
        def wait_for_stable_face(self, *a, **k):
            raise RuntimeError("camera exploded")

    def factory(session, stop_event):
        async def wrapper():
            await main.visual_greeting(BoomDetector(), session, stop_event)
        return wrapper()

    session, stop_event = _run(factory, timeout=6.0)
    assert not stop_event.is_set(), "视觉异常不应结束语音会话"
    assert session.client.tts_calls == []
    print("F PASS: 视觉检测异常被隔离，语音会话不受影响")


if __name__ == "__main__":
    test_A_startup_no_greeting_without_trigger()
    test_B_ending_said_triggers_greeting()
    test_C_silence_triggers_greeting()
    test_D_no_face_no_restart()
    test_E_tts_fail_fallback_to_501()
    test_F_detector_exception_isolated()
    print("\nALL VISUAL GREETING TESTS PASSED")
