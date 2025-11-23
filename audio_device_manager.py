from typing import Any, Optional

import pyaudio

from audio_constants import AudioConfig
from ros_audio import Ros1SpeakerStream


class AudioDeviceManager:
    def __init__(self, input_config: AudioConfig, output_config: AudioConfig):
        self.input_config = input_config
        self.output_config = output_config
        self.pyaudio = pyaudio.PyAudio()
        self.input_stream: Optional[pyaudio.Stream] = None
        self.output_stream: Optional[Any] = None

    def open_input_stream(self) -> pyaudio.Stream:
        open_kwargs = dict(
            format=self.input_config.bit_size,
            channels=self.input_config.channels,
            rate=self.input_config.sample_rate,
            input=True,
            frames_per_buffer=self.input_config.chunk,
        )
        if self.input_config.device_index is not None:
            open_kwargs["input_device_index"] = self.input_config.device_index
        self.input_stream = self.pyaudio.open(**open_kwargs)
        return self.input_stream

    def open_output_stream(self) -> Any:
        mode = (self.output_config.mode or "pyaudio").lower()
        if mode == "pyaudio":
            open_kwargs = dict(
                format=self.output_config.bit_size,
                channels=self.output_config.channels,
                rate=self.output_config.sample_rate,
                output=True,
                frames_per_buffer=self.output_config.chunk,
            )
            if self.output_config.device_index is not None:
                open_kwargs["output_device_index"] = self.output_config.device_index
            self.output_stream = self.pyaudio.open(**open_kwargs)
            return self.output_stream
        elif mode == "ros1":
            self.output_stream = Ros1SpeakerStream(
                topic=self.output_config.ros1_topic,
                node_name=self.output_config.ros1_node_name,
                queue_size=self.output_config.ros1_queue_size,
                latched=self.output_config.ros1_latch,
            )
            return self.output_stream
        else:
            raise ValueError(f"未知输出模式：{mode!r}")

    def cleanup(self) -> None:
        if isinstance(self.input_stream, pyaudio.Stream):
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception:
                pass
        self.input_stream = None
        if self.output_stream is not None:
            try:
                if hasattr(self.output_stream, "stop_stream"):
                    self.output_stream.stop_stream()
                if hasattr(self.output_stream, "close"):
                    self.output_stream.close()
            except Exception:
                pass
        self.output_stream = None
        try:
            self.pyaudio.terminate()
        except Exception:
            pass
