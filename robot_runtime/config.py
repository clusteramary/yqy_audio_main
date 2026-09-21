"""Legacy robot settings. Credentials are read from the process environment."""
import os
import uuid
from pathlib import Path

import pyaudio

runtime_control_path = str(Path(__file__).resolve().parent / "sauc_python" / "ctrl.txt")

ws_connect_config = {
    "base_url": "wss://openspeech.bytedance.com/api/v3/realtime/dialogue",
    "headers": {
        "X-Api-App-ID": os.environ.get("VOLC_DIALOG_APP_ID", ""),
        "X-Api-Access-Key": os.environ.get("VOLC_DIALOG_ACCESS_KEY", ""),
        "X-Api-Resource-Id": "volc.speech.dialog",
        "X-Api-App-Key": os.environ.get("VOLC_DIALOG_APP_KEY", ""),
        "X-Api-Connect-Id": str(uuid.uuid4()),
    },
}
start_session_req = {
    "tts": {"speaker": "zh_female_vv_jupiter_bigtts",
            "audio_config": {"channel": 1, "format": "pcm", "sample_rate": 24000}},
    "dialog": {"bot_name": "华科机器人", "system_role": "你是一名耐心、自然的机器人采访记者。",
               "speaking_style": "简洁清晰，每次只提出一个问题。",
               "location": {"city": "武汉"}},
}
input_audio_config = {
    "chunk": 960, "format": "pcm", "channels": 1, "sample_rate": 48000,
    "bit_size": pyaudio.paInt16, "device_index": int(os.environ.get("ROBOT_MIC_INDEX", "2")),
}
output_audio_config = {
    "chunk": 3200, "format": "pcm", "channels": 1, "sample_rate": 24000,
    "bit_size": pyaudio.paFloat32, "device_index": None,
    "mode": os.environ.get("ROBOT_AUDIO_MODE", "ros1"), "ros1_topic": "/audio",
    "ros1_node_name": "speaker_publisher", "ros1_queue_size": 10, "ros1_latch": False,
}
# Retained wire format must be checked against the robot player before deployment.
