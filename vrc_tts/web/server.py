from __future__ import annotations

import threading
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from vrc_tts.core.config import ConfigManager
from vrc_tts.core.errors import ErrorHandler
from vrc_tts.services.audio_player import list_input_devices, list_output_devices
from vrc_tts.services.osc_client import VrcOscClient
from vrc_tts.services.tts_client import synthesize_tts


def create_web_app(config_manager: ConfigManager, error_handler: ErrorHandler, show_input_callback, reload_hotkey_callback) -> Flask:
    web_dir = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(web_dir / "templates"),
        static_folder=str(web_dir / "static"),
    )

    @app.get("/")
    def index():
        return render_template(
            "settings.html",
            config=config_manager.get(),
            last_error=error_handler.last_error(),
        )

    @app.post("/save")
    def save():
        config_manager.update_from_dict(request.form.to_dict())
        reload_hotkey_callback()
        return redirect(url_for("index"))

    @app.post("/open-input")
    def open_input():
        show_input_callback()
        return redirect(url_for("index"))

    @app.post("/test-osc")
    def test_osc():
        try:
            VrcOscClient(config_manager.get()).send_chatbox("VRC TTS Text OSC 测试")
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
