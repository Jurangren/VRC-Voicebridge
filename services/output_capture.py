from __future__ import annotations

import numpy as np
import soundcard as sc
from soundcard import SoundcardRuntimeWarning
import speech_recognition as sr
import warnings

from core.config import AppConfig
from core.errors import AppError
from services.mic_listener import recognize_speech


_CAPTURE_STATUS = {
    "enabled": False,
    "generation": 0,
    "listening": False,
    "speaking": False,
    "rms": 0.0,
    "peak": 0.0,
}


def list_output_capture_devices() -> list[dict]:
    devices: list[dict] = []
    for index, speaker in enumerate(sc.all_speakers()):
        devices.append(
            {
                "id": speaker.id,
                "index": index,
                "name": speaker.name,
            }
        )
    return devices


def output_capture_status() -> dict:
    return dict(_CAPTURE_STATUS)


def set_output_capture_enabled(enabled: bool) -> None:
    if _CAPTURE_STATUS.get("enabled") != bool(enabled):
        _CAPTURE_STATUS["generation"] = int(_CAPTURE_STATUS.get("generation", 0)) + 1
    _CAPTURE_STATUS["enabled"] = bool(enabled)
    if not enabled:
        _CAPTURE_STATUS.update({"listening": False, "speaking": False, "rms": 0.0, "peak": 0.0})


def recognize_output_once(device_id: str, seconds: float, config: AppConfig) -> str:
    generation = int(_CAPTURE_STATUS.get("generation", 0))
    if not _CAPTURE_STATUS.get("enabled"):
        raise sr.UnknownValueError()
    duration = min(max(float(seconds), 1.0), 30.0)
    sample_rate = 16000
    block_seconds = 0.05
    block_frames = int(sample_rate * block_seconds)
    threshold = min(max(float(config.speech_translate_energy_threshold), 0.0001), 1.0)
    silence_blocks_limit = max(1, int(max(100, int(config.speech_translate_silence_ms)) / (block_seconds * 1000)))
    pre_roll_blocks = max(1, int(0.5 / block_seconds))
    min_speech_blocks = max(1, int(0.25 / block_seconds))
    max_blocks = int(duration / block_seconds)
    pre_roll: list[np.ndarray] = []
    speech_blocks: list[np.ndarray] = []
    speech_started = False
    silence_blocks = 0
    voiced_blocks = 0

    try:
        _CAPTURE_STATUS.update({"enabled": True, "listening": True, "speaking": False, "rms": 0.0, "peak": 0.0})
        speaker = sc.get_speaker(device_id) if device_id else sc.default_speaker()
        loopback = sc.get_microphone(speaker.id, include_loopback=True)
        with loopback.recorder(samplerate=sample_rate, channels=1) as recorder:
            for _ in range(max_blocks):
                if not _CAPTURE_STATUS.get("enabled") or generation != int(_CAPTURE_STATUS.get("generation", 0)):
                    raise sr.UnknownValueError()
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
                    block = np.asarray(recorder.record(numframes=block_frames), dtype=np.float32).reshape(-1)
                if block.size == 0:
                    continue
                rms = float(np.sqrt(np.mean(np.square(block))))
                block_peak = float(np.max(np.abs(block)))
                _CAPTURE_STATUS.update({"rms": rms, "peak": block_peak})
                if speech_started:
                    speech_blocks.append(block)
                    if rms < threshold:
                        silence_blocks += 1
                        if silence_blocks >= silence_blocks_limit:
                            break
                    else:
                        voiced_blocks += 1
                        silence_blocks = 0
                elif rms >= threshold:
                    speech_started = True
                    _CAPTURE_STATUS["speaking"] = True
                    voiced_blocks = 1
                    speech_blocks.extend(pre_roll)
                    speech_blocks.append(block)
                    pre_roll.clear()
                else:
                    pre_roll.append(block)
                    if len(pre_roll) > pre_roll_blocks:
                        pre_roll.pop(0)
    except Exception as exc:
        raise AppError(f"监听输出设备失败：{exc}") from exc
    finally:
        _CAPTURE_STATUS.update({"listening": False, "speaking": False})

    if not speech_blocks or voiced_blocks < min_speech_blocks:
        raise sr.UnknownValueError()

    mono = np.concatenate(speech_blocks)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak < threshold:
        raise sr.UnknownValueError()

    if peak > 0:
        mono = mono * min(1.0 / peak * 0.92, 8.0)

    pcm = (np.clip(mono, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    audio = sr.AudioData(pcm, sample_rate, 2)
    recognizer = sr.Recognizer()
    return recognize_speech(audio, recognizer, config).strip()
