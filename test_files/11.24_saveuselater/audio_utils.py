import wave

import config


def save_input_pcm_to_wav(pcm_data: bytes, filename: str) -> None:
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(config.input_audio_config["channels"])
        wf.setsampwidth(2)
        wf.setframerate(config.input_audio_config["sample_rate"])
        wf.writeframes(pcm_data)


def save_output_to_file(audio_data: bytes, filename: str) -> None:
    if not audio_data:
        print("No audio data to save.")
        return
    try:
        with open(filename, "wb") as f:
            f.write(audio_data)
    except IOError as e:
        print(f"Failed to save pcm file: {e}")
