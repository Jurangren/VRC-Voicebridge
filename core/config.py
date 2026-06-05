from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from threading import RLock
from typing import Any


CONFIG_PATH = Path("config.json")


@dataclass
class AppConfig:
    run_mode: str = "hotkey_input"
    hotkey: str = "b"
    input_hotkey: str = "b"
    microphone_hotkey: str = "v"
    web_host: str = "127.0.0.1"
    web_port: int = 8765

    translation_provider: str = "google"
    source_language: str = "zh-CN"
    target_language: str = "ja"
    bubble_format: str = "{original}\n{translated}"
    translation_retry_count: int = 2
    microsoft_translator_key: str = ""
    microsoft_translator_region: str = ""
    microsoft_translator_endpoint: str = "https://api.cognitive.microsofttranslator.com"
    tencent_translator_secret_id: str = ""
    tencent_translator_secret_key: str = ""
    tencent_translator_region: str = "ap-guangzhou"
    tencent_translator_endpoint: str = "tmt.tencentcloudapi.com"
    baidu_translator_app_id: str = ""
    baidu_translator_secret_key: str = ""
    baidu_translator_endpoint: str = "https://fanyi-api.baidu.com/api/trans/vip/translate"

    overlay_alpha: float = 0.92

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_tts_model: str = "tts-1"
    openai_tts_voice: str = "alloy"
    openai_tts_format: str = "wav"
    tts_retry_count: int = 2
    tts_timeout_seconds: int = 60

    osc_host: str = "127.0.0.1"
    osc_port: int = 9000
    osc_chatbox_path: str = "/chatbox/input"
    osc_typing_path: str = "/chatbox/typing"
    osc_voice_path: str = "/input/Voice"
    osc_chat_enter: bool = True
    osc_chat_notify: bool = False

    virtual_audio_device_keyword: str = "CABLE Input"
    listen_mic_device_index: int = -1
    listen_mic_device_keyword: str = ""
    speech_recognition_provider: str = "google"
    listen_energy_threshold: int = 800
    listen_phrase_time_limit: int = 8
    listen_confirm_timeout_seconds: int = 3
    listen_language: str = "zh-CN"
    tencent_asr_secret_id: str = ""
    tencent_asr_secret_key: str = ""
    tencent_asr_region: str = "ap-guangzhou"
    tencent_asr_endpoint: str = "asr.tencentcloudapi.com"
    tencent_asr_engine_model_type: str = "16k_zh"
    tencent_asr_filter_dirty: int = 0
    tencent_asr_filter_modal: int = 0
    tencent_asr_filter_punc: int = 0
    play_to_speaker: bool = True
    speaker_volume: float = 1.0
    audio_chunk_size: int = 1024


class ConfigManager:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._lock = RLock()
        self._config = AppConfig()
        self.load()

    def load(self) -> AppConfig:
        with self._lock:
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    valid_names = {field.name for field in fields(AppConfig)}
                    cleaned = {key: value for key, value in data.items() if key in valid_names}
                    self._config = AppConfig(**cleaned)
                except Exception:
                    backup_path = self.path.with_suffix(".invalid.json")
                    self.path.replace(backup_path)
                    self._config = AppConfig()
                    self.save(self._config)
            else:
                self.save(self._config)
            return self._config

    def get(self) -> AppConfig:
        with self._lock:
            return AppConfig(**asdict(self._config))

    def save(self, config: AppConfig) -> AppConfig:
        with self._lock:
            self._config = config
            self.path.write_text(
                json.dumps(asdict(config), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return self.get()

    def update_from_dict(self, data: dict[str, Any]) -> AppConfig:
        current = asdict(self.get())
        bool_fields = {
            "osc_chat_enter",
            "osc_chat_notify",
            "play_to_speaker",
        }
        int_fields = {
            "web_port",
            "osc_port",
            "translation_retry_count",
            "tts_retry_count",
            "tts_timeout_seconds",
            "audio_chunk_size",
            "listen_mic_device_index",
            "listen_energy_threshold",
            "listen_phrase_time_limit",
            "listen_confirm_timeout_seconds",
            "tencent_asr_filter_dirty",
            "tencent_asr_filter_modal",
            "tencent_asr_filter_punc",
        }
        float_fields = {
            "overlay_alpha",
            "speaker_volume",
        }

        for key in current:
            if key not in data:
                if key in bool_fields:
                    current[key] = False
                continue

            value = data[key]
            if key in bool_fields:
                current[key] = str(value).lower() in {"1", "true", "on", "yes"}
            elif key == "bubble_format":
                current[key] = str(value).strip().replace("\\n", "\n")
            elif key in int_fields:
                current[key] = int(value)
            elif key in float_fields:
                if key == "speaker_volume":
                    current[key] = min(max(float(value), 0.0), 2.0)
                else:
                    current[key] = min(max(float(value), 0.1), 1.0)
            else:
                current[key] = str(value).strip()

        return self.save(AppConfig(**current))
