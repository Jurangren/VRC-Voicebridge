from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for
import speech_recognition as sr

from core.config import ConfigManager
from core.errors import ErrorHandler
from services.audio_player import list_input_devices, list_output_devices
from services.osc_client import VrcOscClient
from services.output_capture import list_output_capture_devices, output_capture_status, recognize_output_once, set_output_capture_enabled
from services.realtime_pipeline import PIPELINE, list_microphone_devices
from services.tts_client import synthesize_tts
from services.translator import translate_text


def create_web_app(config_manager: ConfigManager, error_handler: ErrorHandler, show_input_callback, reload_hotkey_callback) -> Flask:
    web_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(web_dir / "templates"),
        static_folder=str(web_dir / "static"),
    )
    app.config["speech_indicator"] = None

    @app.get("/")
    def index():
        return render_template(
            "settings.html",
            config=config_manager.get(),
            last_error=error_handler.last_error(),
        )

    @app.get("/speech-translate")
    def speech_translate():
        return render_template(
            "speech_translate.html",
            config=config_manager.get(),
            last_error=error_handler.last_error(),
        )

    @app.post("/save")
    def save():
        config_manager.update_from_dict(request.form.to_dict())
        reload_hotkey_callback()
        return redirect(url_for("index"))

    @app.get("/api/presets")
    def presets():
        return jsonify(config_manager.preset_summary())

    @app.post("/api/presets/meta")
    def save_preset_meta():
        try:
            payload = request.get_json(silent=True) or {}
            config_manager.update_preset_meta(payload)
            reload_hotkey_callback()
            return jsonify({"ok": True, "summary": config_manager.preset_summary()})
        except Exception as exc:
            error_handler.report("保存预设信息失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/presets/<int:index>/save")
    def save_preset(index: int):
        try:
            config_manager.save_current_to_preset(index)
            return jsonify({"ok": True, "summary": config_manager.preset_summary()})
        except Exception as exc:
            error_handler.report("保存当前设置到预设失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/presets/<int:index>/apply")
    def apply_preset(index: int):
        try:
            config = config_manager.apply_preset(index)
            reload_hotkey_callback()
            preset_name = config.preset_names[config.active_preset_index - 1]
            return jsonify(
                {
                    "ok": True,
                    "active": config.active_preset_index,
                    "name": preset_name,
                    "summary": config_manager.preset_summary(),
                }
            )
        except Exception as exc:
            error_handler.report("切换预设失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/open-input")
    def open_input():
        show_input_callback()
        return redirect(url_for("index"))

    @app.post("/test-osc")
    def test_osc():
        try:
            VrcOscClient(config_manager.get()).send_chatbox("VRC VoiceBridge OSC 测试")
            return jsonify({"ok": True})
        except Exception as exc:
            error_handler.report("OSC 测试失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/test-tts")
    def test_tts():
        try:
            path = synthesize_tts("これはTTSテストです。", config_manager.get())
            path.unlink(missing_ok=True)
            return jsonify({"ok": True})
        except Exception as exc:
            error_handler.report("TTS 测试失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/translate")
    def api_translate():
        try:
            payload = request.get_json(silent=True) or {}
            text = str(payload.get("text", "")).strip()
            if not text:
                return jsonify({"ok": False, "error": "待翻译文本不能为空"}), 400

            provider = str(payload.get("provider", "")).strip() or config_manager.get().translation_provider
            source_language = str(payload.get("source_language", "")).strip() or config_manager.get().source_language
            target_language = str(payload.get("target_language", "")).strip() or config_manager.get().target_language
            allowed_providers = {"google", "microsoft", "tencent", "baidu", "local_llm"}
            if provider not in allowed_providers:
                return jsonify({"ok": False, "error": f"不支持的翻译渠道：{provider}"}), 400

            runtime_config = replace(
                config_manager.get(),
                translation_provider=provider,
                source_language=source_language,
                target_language=target_language,
            )
            translated = translate_text(text, runtime_config)
            return jsonify(
                {
                    "ok": True,
                    "original": text,
                    "translated": translated,
                    "provider": provider,
                    "source_language": source_language,
                    "target_language": target_language,
                }
            )
        except Exception as exc:
            error_handler.report("实时语音翻译失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/output-capture/devices")
    def output_capture_devices():
        try:
            return jsonify(list_output_capture_devices())
        except Exception as exc:
            error_handler.report("扫描输出监听设备失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/output-capture/status")
    def output_capture_status_api():
        return jsonify(output_capture_status())

    @app.post("/api/output-capture/enabled")
    def output_capture_enabled_api():
        payload = request.get_json(silent=True) or {}
        set_output_capture_enabled(bool(payload.get("enabled")))
        return jsonify({"ok": True})

    @app.post("/api/output-capture/translate-once")
    def output_capture_translate_once():
        try:
            payload = request.get_json(silent=True) or {}
            config = config_manager.get()
            provider = str(payload.get("provider", "")).strip() or config.speech_translate_translation_provider
            source_language = str(payload.get("source_language", "")).strip() or config.speech_translate_source_language
            target_language = str(payload.get("target_language", "")).strip() or config.speech_translate_target_language
            recognition_language = source_language
            allowed_providers = {"google", "microsoft", "tencent", "baidu", "local_llm"}
            if provider not in allowed_providers:
                return jsonify({"ok": False, "error": f"不支持的翻译渠道：{provider}"}), 400

            runtime_config = replace(
                config,
                translation_provider=provider,
                source_language=source_language,
                target_language=target_language,
                speech_recognition_provider=str(payload.get("recognition_provider", "")).strip()
                or config.speech_translate_recognition_provider,
                listen_language=recognition_language,
                tencent_asr_engine_model_type=str(payload.get("tencent_asr_engine_model_type", "")).strip()
                or config.speech_translate_tencent_asr_engine_model_type,
                speech_translate_local_whisper_model=str(payload.get("local_whisper_model", "")).strip()
                or config.speech_translate_local_whisper_model,
                speech_translate_chunk_seconds=float(payload.get("chunk_seconds", config.speech_translate_chunk_seconds)),
                speech_translate_energy_threshold=float(payload.get("energy_threshold", config.speech_translate_energy_threshold)),
                speech_translate_silence_ms=int(payload.get("silence_ms", config.speech_translate_silence_ms)),
            )
            runtime_config.speech_recognition_provider = str(payload.get("recognition_provider", "")).strip() or config.speech_translate_recognition_provider
            if runtime_config.speech_recognition_provider not in {"google", "tencent", "local_whisper_gpu"}:
                return jsonify({"ok": False, "error": f"不支持的语音识别渠道：{runtime_config.speech_recognition_provider}"}), 400
            if not output_capture_status().get("enabled"):
                return jsonify({"ok": False, "silent": True, "error": "实时翻译已停止"}), 200
            original = recognize_output_once(
                str(payload.get("device_id", "")).strip(),
                float(payload.get("chunk_seconds", 4)),
                runtime_config,
            )
            if not original:
                return jsonify({"ok": False, "silent": True, "error": "未识别到输出设备音频"}), 200
            if not output_capture_status().get("enabled"):
                return jsonify({"ok": False, "silent": True, "error": "实时翻译已停止"}), 200
            translated = translate_text(original, runtime_config)
            indicator = app.config.get("speech_indicator")
            if indicator is not None:
                indicator.show_text(
                    translated,
                    float(config.speech_translate_overlay_text_seconds),
                    float(config.speech_translate_overlay_text_alpha),
                )
            return jsonify(
                {
                    "ok": True,
                    "original": original,
                    "translated": translated,
                    "provider": provider,
                    "source_language": source_language,
                    "target_language": target_language,
                }
            )
        except sr.UnknownValueError:
            return jsonify({"ok": False, "silent": True, "error": "未识别到输出设备音频"}), 200
        except Exception as exc:
            error_handler.report("输出设备实时翻译失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/speech-translate/devices")
    def speech_translate_devices():
        try:
            return jsonify({"microphones": list_microphone_devices(), "outputs": list_output_capture_devices()})
        except Exception as exc:
            error_handler.report("扫描实时翻译音频设备失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/speech-translate/start")
    def speech_translate_start():
        try:
            payload = request.get_json(silent=True) or {}
            config = config_manager.get()
            provider = str(payload.get("provider", "")).strip() or config.speech_translate_translation_provider
            source_language = str(payload.get("source_language", "")).strip() or config.speech_translate_source_language
            target_language = str(payload.get("target_language", "")).strip() or config.speech_translate_target_language
            if provider not in {"google", "microsoft", "tencent", "baidu", "local_llm"}:
                return jsonify({"ok": False, "error": f"不支持的翻译渠道：{provider}"}), 400
            audio_source = str(payload.get("audio_source", "")).strip() or config.speech_translate_audio_source
            if audio_source not in {"microphone", "output"}:
                return jsonify({"ok": False, "error": f"不支持的音频来源：{audio_source}"}), 400

            # 只写 speech_translate_* 字段；映射到 translation_provider/source/target 等
            # 通用字段由 PIPELINE.start -> build_realtime_runtime_config 统一完成
            runtime_config = replace(
                config,
                speech_translate_translation_provider=provider,
                speech_translate_source_language=source_language,
                speech_translate_target_language=target_language,
                speech_translate_audio_source=audio_source,
                speech_translate_mic_device_id=str(payload.get("mic_device_id", config.speech_translate_mic_device_id)).strip(),
                speech_translate_output_device_id=str(payload.get("output_device_id", config.speech_translate_output_device_id)).strip(),
                speech_translate_chunk_seconds=float(payload.get("chunk_seconds", config.speech_translate_chunk_seconds)),
                speech_translate_silence_ms=int(payload.get("silence_ms", config.speech_translate_silence_ms)),
                speech_translate_vad_threshold=float(payload.get("vad_threshold", config.speech_translate_vad_threshold)),
                speech_translate_min_speech_ms=int(payload.get("min_speech_ms", config.speech_translate_min_speech_ms)),
                speech_translate_speaker_enabled=bool(payload.get("speaker_enabled", config.speech_translate_speaker_enabled)),
                speech_translate_speaker_similarity=float(
                    payload.get("speaker_similarity", config.speech_translate_speaker_similarity)
                ),
                speech_translate_max_speakers=int(payload.get("max_speakers", config.speech_translate_max_speakers)),
                speech_translate_speaker_model_path=str(
                    payload.get("speaker_model_path", config.speech_translate_speaker_model_path)
                ).strip(),
                speech_translate_local_whisper_model=str(payload.get("local_whisper_model", "")).strip()
                or config.speech_translate_local_whisper_model,
                speech_translate_hotwords=str(payload.get("hotwords", config.speech_translate_hotwords)).strip(),
                speech_translate_osc_enabled=bool(payload.get("osc_enabled", config.speech_translate_osc_enabled)),
                speech_translate_osc_format=str(
                    payload.get("osc_format", config.speech_translate_osc_format)
                ).replace("\\n", "\n"),
                speech_translate_osc_user_hold_seconds=float(
                    payload.get("osc_user_hold_seconds", config.speech_translate_osc_user_hold_seconds)
                ),
            )
            PIPELINE.start(runtime_config, indicator=app.config.get("speech_indicator"))
            return jsonify({"ok": True})
        except Exception as exc:
            error_handler.report("启动实时翻译失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/speech-translate/stop")
    def speech_translate_stop():
        try:
            PIPELINE.stop()
            return jsonify({"ok": True})
        except Exception as exc:
            error_handler.report("停止实时翻译失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/api/speech-translate/stream")
    def speech_translate_stream():
        after = request.args.get("after", 0, type=int)
        return jsonify({"ok": True, "status": PIPELINE.status(), "events": PIPELINE.events_after(after)})

    @app.get("/api/speech-translate/config")
    def speech_translate_config():
        config = config_manager.get()
        return jsonify(
            {
                "output_device_id": config.speech_translate_output_device_id,
                "audio_source": config.speech_translate_audio_source,
                "mic_device_id": config.speech_translate_mic_device_id,
                "vad_threshold": config.speech_translate_vad_threshold,
                "min_speech_ms": config.speech_translate_min_speech_ms,
                "speaker_enabled": config.speech_translate_speaker_enabled,
                "speaker_similarity": config.speech_translate_speaker_similarity,
                "max_speakers": config.speech_translate_max_speakers,
                "speaker_model_path": config.speech_translate_speaker_model_path,
                "chunk_seconds": config.speech_translate_chunk_seconds,
                "energy_threshold": config.speech_translate_energy_threshold,
                "silence_ms": config.speech_translate_silence_ms,
                "overlay_text_seconds": config.speech_translate_overlay_text_seconds,
                "overlay_text_alpha": config.speech_translate_overlay_text_alpha,
                "osc_enabled": config.speech_translate_osc_enabled,
                "osc_format": config.speech_translate_osc_format.replace("\n", "\\n"),
                "osc_user_hold_seconds": config.speech_translate_osc_user_hold_seconds,
                "osc_toggle_hotkey": config.speech_translate_osc_toggle_hotkey,
                "recognition_provider": config.speech_translate_recognition_provider,
                "recognition_language": config.speech_translate_source_language,
                "tencent_asr_engine_model_type": config.speech_translate_tencent_asr_engine_model_type,
                "local_whisper_model": config.speech_translate_local_whisper_model,
                "hotwords": config.speech_translate_hotwords,
                "source_language": config.speech_translate_source_language,
                "target_language": config.speech_translate_target_language,
                "provider": config.speech_translate_translation_provider,
            }
        )

    @app.post("/api/speech-translate/config")
    def save_speech_translate_config():
        try:
            payload = request.get_json(silent=True) or {}
            config_manager.patch_from_dict(
                {
                    "speech_translate_output_device_id": str(payload.get("output_device_id", "")).strip(),
                    "speech_translate_audio_source": str(payload.get("audio_source", "output")).strip(),
                    "speech_translate_mic_device_id": str(payload.get("mic_device_id", "")).strip(),
                    "speech_translate_chunk_seconds": payload.get("chunk_seconds", 8),
                    "speech_translate_silence_ms": payload.get("silence_ms", 900),
                    "speech_translate_vad_threshold": payload.get("vad_threshold", 0.5),
                    "speech_translate_min_speech_ms": payload.get("min_speech_ms", 300),
                    "speech_translate_speaker_enabled": bool(payload.get("speaker_enabled", True)),
                    "speech_translate_speaker_similarity": payload.get("speaker_similarity", 0.6),
                    "speech_translate_max_speakers": payload.get("max_speakers", 6),
                    "speech_translate_speaker_model_path": str(payload.get("speaker_model_path", "")).strip(),
                    "speech_translate_overlay_text_seconds": payload.get("overlay_text_seconds", 6),
                    "speech_translate_overlay_text_alpha": payload.get("overlay_text_alpha", 0.78),
                    "speech_translate_osc_enabled": bool(payload.get("osc_enabled", False)),
                    "speech_translate_osc_format": str(payload.get("osc_format", "{translated}")),
                    "speech_translate_osc_user_hold_seconds": payload.get("osc_user_hold_seconds", 10),
                    "speech_translate_osc_toggle_hotkey": str(payload.get("osc_toggle_hotkey", "")).strip(),
                    "speech_translate_recognition_provider": str(payload.get("recognition_provider", "")).strip(),
                    "speech_translate_recognition_language": str(payload.get("source_language", "")).strip(),
                    "speech_translate_tencent_asr_engine_model_type": str(
                        payload.get("tencent_asr_engine_model_type", "")
                    ).strip(),
                    "speech_translate_local_whisper_model": str(payload.get("local_whisper_model", "")).strip(),
                    "speech_translate_hotwords": str(payload.get("hotwords", "")).strip(),
                    "speech_translate_source_language": str(payload.get("source_language", "")).strip(),
                    "speech_translate_target_language": str(payload.get("target_language", "")).strip(),
                    "speech_translate_translation_provider": str(payload.get("provider", "")).strip(),
                }
            )
            reload_hotkey_callback()
            return jsonify({"ok": True})
        except Exception as exc:
            error_handler.report("保存实时语音翻译配置失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.post("/api/models/download")
    def models_download():
        from services.model_downloader import MODEL_DOWNLOADER

        payload = request.get_json(silent=True) or {}
        target = str(payload.get("target", "")).strip()
        if target not in {"speaker", "whisper"}:
            return jsonify({"ok": False, "error": "未知下载目标"}), 400
        if not MODEL_DOWNLOADER.start(target, config_manager.get()):
            return jsonify({"ok": False, "error": "已有下载任务在进行中"}), 409
        return jsonify({"ok": True})

    @app.get("/api/models/status")
    def models_status():
        from services.model_downloader import MODEL_DOWNLOADER

        return jsonify(MODEL_DOWNLOADER.status())

    @app.get("/devices")
    def devices():
        try:
            return jsonify({"output": list_output_devices(), "input": list_input_devices()})
        except Exception as exc:
            error_handler.report("扫描音频设备失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.get("/devices/input")
    def input_devices():
        try:
            return jsonify(list_input_devices())
        except Exception as exc:
            error_handler.report("扫描输入设备失败", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    return app


def start_web_server(app: Flask, host: str, port: int) -> threading.Thread:
    thread = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    thread.start()
    return thread
