import asyncio
import threading
from typing import Optional

import config
from audio_constants import AudioConfig  # noqa: F401
from audio_device_manager import AudioDeviceManager  # noqa: F401
from audio_utils import save_input_pcm_to_wav, save_output_to_file  # noqa: F401
from dialog_session import DialogSession, _to_thread  # noqa: F401


class VoiceDialogHandle:
    def __init__(
        self,
        thread: threading.Thread,
        loop: asyncio.AbstractEventLoop,
        stop_event: asyncio.Event,
    ):
        self._thread = thread
        self._loop = loop
        self._stop_event = stop_event

    def stop(self):
        if self._loop.is_closed():
            return

        def _set():
            if not self._stop_event.is_set():
                self._stop_event.set()

        self._loop.call_soon_threadsafe(_set)

    def join(self, timeout: Optional[float] = None):
        self._thread.join(timeout)


def _run_session_thread(
    start_prompt: str, audio_file_path: str, stop_event: asyncio.Event
):
    """
    保留原来的辅助函数（即使外部目前未调用），保证功能不变。
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = DialogSession(
        ws_config=config.ws_connect_config,
        start_prompt=start_prompt,
        output_audio_format="pcm",
        audio_file_path=audio_file_path,
    )
    session.attach_stop_event(stop_event)
    try:
        loop.run_until_complete(session.start())
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()


def start_voice_dialog(
    start_prompt: str, audio_file_path: str = ""
) -> VoiceDialogHandle:
    stop_event = asyncio.Event()
    loop = asyncio.new_event_loop()
    loop.close()
    thread = threading.Thread(
        target=_thread_entry,
        args=(start_prompt, audio_file_path, stop_event),
        daemon=True,
    )
    thread.start()
    placeholder_loop = asyncio.new_event_loop()
    handle = VoiceDialogHandle(
        thread=thread, loop=placeholder_loop, stop_event=stop_event
    )
    return handle


def _thread_entry(start_prompt: str, audio_file_path: str, stop_event: asyncio.Event):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    session = DialogSession(
        ws_config=config.ws_connect_config,
        start_prompt=start_prompt,
        output_audio_format="pcm",
        audio_file_path=audio_file_path,
    )
    session.attach_stop_event(stop_event)
    try:
        loop.run_until_complete(session.start())
    finally:
        pending = asyncio.all_tasks(loop)
        for t in pending:
            t.cancel()
        try:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        loop.close()
