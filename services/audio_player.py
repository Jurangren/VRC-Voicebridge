from __future__ import annotations

import audioop
import wave
from pathlib import Path

import pyaudio

from core.config import AppConfig
from core.errors import AppError


def list_output_devices() -> list[dict]:
    p = pyaudio.PyAudio()
    devices: list[dict] = []
    try:
        for index in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(index)
            except OSError:
                continue
            if info.get("maxOutputChannels", 0) > 0:
                devices.append(
                    {
                        "index": index,
                        "name": info.get("name", ""),
                        "channels": info.get("maxOutputChannels", 0),
                        "rate": info.get("defaultSampleRate", 0),
                    }
                )
    finally:
        p.terminate()
    return devices


def list_input_devices() -> list[dict]:
    p = pyaudio.PyAudio()
    devices: list[dict] = []
    try:
        for index in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(index)
            except OSError:
                continue
            if info.get("maxInputChannels", 0) > 0:
                devices.append(
                    {
                        "index": index,
                        "name": info.get("name", ""),
                        "channels": info.get("maxInputChannels", 0),
                        "rate": info.get("defaultSampleRate", 0),
                    }
                )
    finally:
        p.terminate()
    return devices


def find_input_device(keyword: str, device_index: int = -1) -> int | None:
    if device_index >= 0:
        return device_index
    if not keyword.strip():
        return None
    p = pyaudio.PyAudio()
    try:
        for index in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(index)
            except OSError:
                continue
            if keyword.lower() in info.get("name", "").lower() and info.get("maxInputChannels", 0) > 0:
                return index
    finally:
        p.terminate()
    raise AppError(f"未找到监听麦克风输入设备：{keyword}，请检查设置面板中的设备关键字")


def find_output_device(keyword: str) -> int:
    p = pyaudio.PyAudio()
    try:
        for index in range(p.get_device_count()):
            try:
                info = p.get_device_info_by_index(index)
            except OSError:
                continue
            if keyword.lower() in info.get("name", "").lower() and info.get("maxOutputChannels", 0) > 0:
                return index
    finally:
        p.terminate()
    raise AppError(f"未找到虚拟麦克风输出设备：{keyword}，请检查 VB-Cable 或设置面板中的设备关键字")


def play_audio_to_virtual_mic(wav_path: Path, config: AppConfig) -> None:
    if (config.openai_tts_format or "wav").lower() != "wav":
        raise AppError("当前播放器只支持 WAV。请在设置面板把 OpenAI TTS 输出格式设为 wav")

    target_idx = find_output_device(config.virtual_audio_device_keyword)
    p = pyaudio.PyAudio()
    wf = None
    stream_mic = None
    stream_speaker = None
    try:
        wf = wave.open(str(wav_path), "rb")
        stream_format = p.get_format_from_width(wf.getsampwidth())
        channels = wf.getnchannels()
        rate = wf.getframerate()

        stream_mic = p.open(
            format=stream_format,
            channels=channels,
            rate=rate,
            output=True,
            output_device_index=target_idx,
        )

        if config.play_to_speaker:
            stream_speaker = p.open(
                format=stream_format,
                channels=channels,
                rate=rate,
                output=True,
            )

        data = wf.readframes(config.audio_chunk_size)
        speaker_volume = min(max(float(config.speaker_volume), 0.0), 2.0)
        while data:
            stream_mic.write(data)
            if stream_speaker is not None:
                stream_speaker.write(audioop.mul(data, wf.getsampwidth(), speaker_volume))
            data = wf.readframes(config.audio_chunk_size)
    except wave.Error as exc:
        raise AppError(f"TTS 音频不是合法 WAV：{exc}") from exc
    except AppError:
        raise
    except Exception as exc:
        raise AppError(f"播放到虚拟麦克风失败：{exc}") from exc
    finally:
        if stream_mic is not None:
            stream_mic.stop_stream()
            stream_mic.close()
        if stream_speaker is not None:
            stream_speaker.stop_stream()
            stream_speaker.close()
        if wf is not None:
            wf.close()
        p.terminate()
