from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from threading import RLock
from typing import Any


CONFIG_PATH = Path("config.json")
PRESET_COUNT = 5
PRESET_EXCLUDED_KEYS = {
    "active_preset_index",
    "preset_next_hotkey",
    "preset_names",
    "preset_hotkeys",
    "preset_snapshots",
}


def _default_preset_names() -> list[str]:
    return [f"预设 {index}" for index in range(1, PRESET_COUNT + 1)]


def _default_preset_hotkeys() -> list[str]:
    return [f"ctrl+alt+{index}" for index in range(1, PRESET_COUNT + 1)]


def _default_preset_snapshots() -> list[dict[str, Any]]:
    return [{} for _ in range(PRESET_COUNT)]


@dataclass
class AppConfig:
    run_mode: str = "hotkey_input"
    hotkey: str = "b"
    input_hotkey: str = "b"
    microphone_hotkey: str = "v"
    preset_next_hotkey: str = "ctrl+alt+0"
    active_preset_index: int = 1
    preset_names: list[str] = field(default_factory=_default_preset_names)
    preset_hotkeys: list[str] = field(default_factory=_default_preset_hotkeys)
    preset_snapshots: list[dict[str, Any]] = field(default_factory=_default_preset_snapshots)
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
    local_llm_endpoint: str = "http://127.0.0.1:11434"
    local_llm_model: str = "qwen3.5:4b"
    local_llm_api_key: str = ""
    local_llm_timeout_seconds: int = 30
    local_llm_prompt: str = (
        "你是翻译引擎。把用户发来的{source}内容翻译成{target}。"
        "只输出译文本身，不要解释、不要注音、不要重复原文。保持口语化、自然，符合日常对话语气。"
    )

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
    mic_vad_mode: bool = False
    mic_vad_device_id: str = ""
    mic_vad_threshold: float = 0.5
    mic_vad_silence_ms: int = 600
    speech_translate_output_device_id: str = ""
    speech_translate_audio_source: str = "output"
    speech_translate_mic_device_id: str = ""
    speech_translate_chunk_seconds: float = 8.0
    speech_translate_energy_threshold: float = 0.01
    speech_translate_silence_ms: int = 900
    speech_translate_vad_threshold: float = 0.5
    speech_translate_min_speech_ms: int = 300
    speech_translate_speaker_enabled: bool = True
    speech_translate_speaker_similarity: float = 0.6
    speech_translate_max_speakers: int = 6
    speech_translate_speaker_model_path: str = ""
    speech_translate_recognition_provider: str = "google"
    speech_translate_recognition_language: str = "ja"
    speech_translate_source_language: str = "ja"
    speech_translate_target_language: str = "zh-CN"
    speech_translate_translation_provider: str = "google"
    speech_translate_tencent_asr_engine_model_type: str = "16k_ja"
    speech_translate_local_whisper_model: str = "large-v3-turbo"
    speech_translate_hotwords: str = ""
    speech_translate_overlay_text_seconds: float = 6.0
    speech_translate_overlay_text_alpha: float = 0.78
    speech_translate_osc_enabled: bool = False
    speech_translate_osc_format: str = "{translated}"
    speech_translate_osc_user_hold_seconds: float = 10.0
    speech_translate_osc_toggle_hotkey: str = ""
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
                    self._config = self._normalize_config(AppConfig(**cleaned))
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
            self._config = self._normalize_config(config)
            self.path.write_text(json.dumps(asdict(self._config), ensure_ascii=False, indent=2), encoding="utf-8")
            return self.get()

    def update_from_dict(self, data: dict[str, Any]) -> AppConfig:
        current = asdict(self.get())
        for key in current:
            if key not in data:
                # 主设置页表单不包含 speech_translate_* 开关，缺失时不要把它们重置为 False
                if key in _bool_fields() and not key.startswith("speech_translate_"):
                    current[key] = False
                continue
            current[key] = _coerce_value(key, data[key])
        self._sync_active_snapshot(current)
        return self.save(AppConfig(**current))

    def patch_from_dict(self, data: dict[str, Any]) -> AppConfig:
        current = asdict(self.get())
        for key, value in data.items():
            if key in current:
                current[key] = _coerce_value(key, value)
        self._sync_active_snapshot(current)
        return self.save(AppConfig(**current))

    def preset_summary(self) -> dict[str, Any]:
        config = self.get()
        return {
            "active": config.active_preset_index,
            "next_hotkey": config.preset_next_hotkey,
            "presets": [
                {
                    "index": index + 1,
                    "name": _safe_list_get(config.preset_names, index, f"预设 {index + 1}"),
                    "hotkey": _safe_list_get(config.preset_hotkeys, index, f"ctrl+alt+{index + 1}"),
                    "has_snapshot": bool(_safe_list_get(config.preset_snapshots, index, {})),
                }
                for index in range(PRESET_COUNT)
            ],
        }

    def update_preset_meta(self, data: dict[str, Any]) -> AppConfig:
        current = asdict(self.get())
        if "preset_next_hotkey" in data:
            current["preset_next_hotkey"] = str(data["preset_next_hotkey"]).strip()
        names = _normalize_string_list(current.get("preset_names"), _default_preset_names())
        hotkeys = _normalize_string_list(current.get("preset_hotkeys"), _default_preset_hotkeys())
        for index in range(PRESET_COUNT):
            if f"preset_{index + 1}_name" in data:
                names[index] = str(data[f"preset_{index + 1}_name"]).strip() or f"预设 {index + 1}"
            if f"preset_{index + 1}_hotkey" in data:
                hotkeys[index] = str(data[f"preset_{index + 1}_hotkey"]).strip()
        current["preset_names"] = names
        current["preset_hotkeys"] = hotkeys
        return self.save(AppConfig(**current))

    def save_current_to_preset(self, index: int) -> AppConfig:
        preset_index = _normalize_preset_index(index)
        current = asdict(self.get())
        snapshots = _normalize_snapshots(current.get("preset_snapshots"))
        snapshots[preset_index - 1] = {key: value for key, value in current.items() if key not in PRESET_EXCLUDED_KEYS}
        current["preset_snapshots"] = snapshots
        current["active_preset_index"] = preset_index
        return self.save(AppConfig(**current))

    def apply_preset(self, index: int) -> AppConfig:
        preset_index = _normalize_preset_index(index)
        current = asdict(self.get())
        snapshots = _normalize_snapshots(current.get("preset_snapshots"))
        active_index = _normalize_preset_index(current.get("active_preset_index", 1))
        snapshots[active_index - 1] = {key: value for key, value in current.items() if key not in PRESET_EXCLUDED_KEYS}
        snapshot = snapshots[preset_index - 1]
        if not snapshot:
            snapshot = {key: value for key, value in current.items() if key not in PRESET_EXCLUDED_KEYS}
            snapshots[preset_index - 1] = snapshot
        for key, value in snapshot.items():
            if key in current and key not in PRESET_EXCLUDED_KEYS:
                current[key] = value
        current["preset_snapshots"] = snapshots
        current["active_preset_index"] = preset_index
        return self.save(AppConfig(**current))

    def apply_next_preset(self) -> AppConfig:
        current = self.get()
        return self.apply_preset((int(current.active_preset_index) % PRESET_COUNT) + 1)

    def _sync_active_snapshot(self, current: dict[str, Any]) -> None:
        snapshots = _normalize_snapshots(current.get("preset_snapshots"))
        active_index = _normalize_preset_index(current.get("active_preset_index", 1))
        snapshots[active_index - 1] = {key: value for key, value in current.items() if key not in PRESET_EXCLUDED_KEYS}
        current["preset_snapshots"] = snapshots

    def _normalize_config(self, config: AppConfig) -> AppConfig:
        data = asdict(config)
        data["active_preset_index"] = _normalize_preset_index(data.get("active_preset_index", 1))
        data["preset_names"] = _normalize_string_list(data.get("preset_names"), _default_preset_names())
        data["preset_hotkeys"] = _normalize_string_list(data.get("preset_hotkeys"), _default_preset_hotkeys())
        data["preset_snapshots"] = _normalize_snapshots(data.get("preset_snapshots"))
        return AppConfig(**data)


def _bool_fields() -> set[str]:
    return {
        "osc_chat_enter", "osc_chat_notify", "play_to_speaker",
        "speech_translate_speaker_enabled", "speech_translate_osc_enabled", "mic_vad_mode",
    }


def _int_fields() -> set[str]:
    return {
        "web_port", "osc_port", "translation_retry_count", "tts_retry_count", "tts_timeout_seconds",
        "audio_chunk_size", "listen_mic_device_index", "listen_energy_threshold", "listen_phrase_time_limit",
        "listen_confirm_timeout_seconds", "local_llm_timeout_seconds",
        "mic_vad_silence_ms", "speech_translate_silence_ms", "speech_translate_min_speech_ms",
        "speech_translate_max_speakers", "tencent_asr_filter_dirty",
        "tencent_asr_filter_modal", "tencent_asr_filter_punc", "active_preset_index",
    }


def _float_fields() -> set[str]:
    return {
        "overlay_alpha", "speaker_volume", "speech_translate_chunk_seconds", "speech_translate_energy_threshold",
        "mic_vad_threshold", "speech_translate_vad_threshold", "speech_translate_speaker_similarity",
        "speech_translate_overlay_text_seconds", "speech_translate_overlay_text_alpha",
        "speech_translate_osc_user_hold_seconds",
    }


def _coerce_value(key: str, value: Any) -> Any:
    if key in _bool_fields():
        return str(value).lower() in {"1", "true", "on", "yes"}
    if key in {"bubble_format", "speech_translate_osc_format", "local_llm_prompt"}:
        return str(value).strip().replace("\\n", "\n")
    if key in _int_fields():
        return int(value)
    if key in _float_fields():
        number = float(value)
        if key == "speaker_volume":
            return min(max(number, 0.0), 2.0)
        if key == "speech_translate_chunk_seconds":
            return min(max(number, 1.0), 30.0)
        if key == "speech_translate_energy_threshold":
            return min(max(number, 0.0001), 1.0)
        if key == "speech_translate_overlay_text_seconds":
            return min(max(number, 1.0), 30.0)
        if key == "speech_translate_osc_user_hold_seconds":
            return min(max(number, 0.0), 120.0)
        return min(max(number, 0.1), 1.0)
    if key in {"preset_names", "preset_hotkeys", "preset_snapshots"}:
        return value
    return str(value).strip()


def _safe_list_get(values: list[Any], index: int, default: Any) -> Any:
    try:
        value = values[index]
    except (IndexError, TypeError):
        return default
    return default if value is None else value


def _normalize_preset_index(index: int) -> int:
    return min(max(int(index), 1), PRESET_COUNT)


def _normalize_string_list(value: Any, default: list[str]) -> list[str]:
    values = list(value or default)[:PRESET_COUNT]
    while len(values) < PRESET_COUNT:
        values.append(default[len(values)])
    return [str(item) for item in values]


def _normalize_snapshots(value: Any) -> list[dict[str, Any]]:
    snapshots = list(value or [])[:PRESET_COUNT]
    while len(snapshots) < PRESET_COUNT:
        snapshots.append({})
    return [snapshot if isinstance(snapshot, dict) else {} for snapshot in snapshots]
