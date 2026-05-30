from __future__ import annotations

import tkinter as tk
import time
import webbrowser

from core.config import ConfigManager
from core.errors import ErrorHandler
from core.pipeline import AppPipeline
from services.mic_listener import MicrophoneListener
from services.osc_client import VrcOscClient
from ui.hotkey import HotkeyManager
from ui.input_window import InputWindow
from ui.status_overlay import StatusOverlay
from ui.tray_app import TrayApp
from web.server import create_web_app, start_web_server


class Application:
    TYPING_REFRESH_MS = 1500

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("VRC VoiceBridge")

        self.config_manager = ConfigManager()
        self.error_handler = ErrorHandler(self.root)
        self.status_overlay = StatusOverlay(self.root, self.config_manager)
        self.pipeline = AppPipeline(
            self.config_manager,
            self.error_handler,
            progress_callback=self.status_overlay.show_progress,
            done_callback=self.status_overlay.show_done,
            error_callback=self.status_overlay.show_error,
            before_audio_callback=self.hide_typing_bubble,
            finish_callback=self.hide_typing_bubble,
        )
        self.input_window = InputWindow(
            self.root,
            self.pipeline.submit,
            on_show=self.show_typing_bubble,
            on_hide=self.hide_typing_bubble,
            on_submit_hide=self.hide_input_window_only,
        )
        self.input_hotkey_manager = HotkeyManager(self.show_input)
        self.microphone_hotkey_manager = HotkeyManager(self.on_microphone_hotkey_press)
        self.tray = TrayApp(self.config_manager, self.show_input, self.quit)
        self._typing_job: str | None = None
        self.mic_listener: MicrophoneListener | None = None
        self._mic_hotkey_pressed = False
        self._pending_mic_text = ""
        self._pending_mic_started_at: float | None = None
        self._pending_mic_job: str | None = None
        self._pending_mic_countdown_seconds = 0

    def start(self) -> None:
        config = self.config_manager.get()
        app = create_web_app(
            self.config_manager,
            self.error_handler,
            self.show_input,
            self.reload_runtime_mode,
        )
        start_web_server(app, config.web_host, config.web_port)
        self.tray.start()
        self.reload_runtime_mode()
        webbrowser.open(f"http://{config.web_host}:{config.web_port}/")
        self.root.mainloop()

    def show_input(self) -> None:
        self.root.after(0, self.toggle_input)

    def toggle_input(self) -> None:
        if self.input_window.is_visible():
            self.input_window.hide()
            return
        initial_text = self._consume_pending_microphone_text()
        self.input_window.show(initial_text)

    def show_typing_bubble(self) -> None:
        if self._typing_job is not None:
            return
        self._send_typing_state(True)
        self._typing_job = self.root.after(self.TYPING_REFRESH_MS, self._refresh_typing_bubble)

    def _refresh_typing_bubble(self) -> None:
        self._typing_job = None
        self.show_typing_bubble()

    def _send_typing_state(self, enabled: bool) -> None:
        try:
            VrcOscClient(self.config_manager.get()).set_typing(enabled)
        except Exception as exc:
            action = "发送" if enabled else "关闭"
            self.error_handler.report(f"{action}正在输入状态失败", exc)

    def hide_typing_bubble(self) -> None:
        if self._typing_job is not None:
            self.root.after_cancel(self._typing_job)
            self._typing_job = None
        self._send_typing_state(False)

    def hide_input_window_only(self) -> None:
        self.input_window.hide(notify=False)

    def reload_hotkey(self) -> None:
        try:
            config = self.config_manager.get()
            self.input_hotkey_manager.register(config.input_hotkey or config.hotkey)
        except Exception as exc:
            self.error_handler.report("输入框热键注册失败", exc)

    def reload_runtime_mode(self) -> None:
        config = self.config_manager.get()
        self.input_hotkey_manager.unregister()
        self.microphone_hotkey_manager.unregister()
        self.stop_microphone_listener()
        self._clear_pending_microphone_text()
        self.start_microphone_listener(config)
        self.reload_hotkey()
        self.reload_microphone_hotkey()

    def reload_microphone_hotkey(self) -> None:
        try:
            config = self.config_manager.get()
            self.microphone_hotkey_manager.register(
                config.microphone_hotkey,
                release_callback=self.on_microphone_hotkey_release,
            )
        except Exception as exc:
            self.error_handler.report("麦克风热键注册失败", exc)

    def start_microphone_listener(self, config) -> None:
        self.mic_listener = MicrophoneListener(
            config,
            text_callback=self.on_microphone_text,
            error_callback=lambda exc: self.error_handler.report("麦克风监听失败", exc),
            finish_callback=self.on_microphone_capture_finish,
        )

    def on_microphone_hotkey_press(self) -> None:
        self.root.after(0, self._handle_microphone_hotkey_press)

    def on_microphone_hotkey_release(self) -> None:
        self.root.after(0, self._handle_microphone_hotkey_release)

    def _handle_microphone_hotkey_press(self) -> None:
        if self._mic_hotkey_pressed:
            return
        self._mic_hotkey_pressed = True
        if self._pending_mic_text:
            text = self._pending_mic_text
            started_at = self._pending_mic_started_at
            self._clear_pending_microphone_text()
            self.show_typing_bubble()
            self.status_overlay.show_progress(0, self.pipeline.TOTAL_STEPS, f"确认发送：{text}")
            self.root.after(150, lambda: self.pipeline.submit(text, started_at))
            return
        if self.mic_listener is None:
            return
        if self.mic_listener.start_capture():
            self._pending_mic_started_at = time.perf_counter()
            self.show_typing_bubble()
            self.status_overlay.show_progress(0, self.pipeline.TOTAL_STEPS, "正在按键录音，松开后识别文字...")

    def _handle_microphone_hotkey_release(self) -> None:
        self._mic_hotkey_pressed = False
        if self.mic_listener is not None:
            self.mic_listener.stop_capture()
        self.status_overlay.show_progress(0, self.pipeline.TOTAL_STEPS, "录音结束，正在识别文字...")

    def stop_microphone_listener(self) -> None:
        if self.mic_listener is not None:
            self.mic_listener.stop()
            self.mic_listener = None
        self._mic_hotkey_pressed = False

    def on_microphone_text(self, text: str) -> None:
        def submit_later() -> None:
            self._set_pending_microphone_text(text)

        self.root.after(0, submit_later)

    def on_microphone_capture_finish(self, recognized: bool) -> None:
        if recognized:
            return
        def finish_later() -> None:
            self.hide_typing_bubble()
            self.status_overlay.show_warning("未识别到语音", hide_after_ms=1800)

        self.root.after(0, finish_later)

    def _set_pending_microphone_text(self, text: str) -> None:
        self._clear_pending_microphone_text()
        self._pending_mic_text = text
        self._pending_mic_countdown_seconds = max(1, int(self.config_manager.get().listen_confirm_timeout_seconds))
        self._refresh_pending_microphone_countdown()

    def _refresh_pending_microphone_countdown(self) -> None:
        if not self._pending_mic_text:
            return
        self.status_overlay.show_progress(
            0,
            self.pipeline.TOTAL_STEPS,
            f"{self._pending_mic_text}\n\n{self._pending_mic_countdown_seconds}秒内再按热键发送",
        )
        if self._pending_mic_countdown_seconds <= 0:
            self._expire_pending_microphone_text()
            return
        self._pending_mic_countdown_seconds -= 1
        self._pending_mic_job = self.root.after(1000, self._refresh_pending_microphone_countdown)

    def _expire_pending_microphone_text(self) -> None:
        self._pending_mic_job = None
        self._pending_mic_text = ""
        self._pending_mic_started_at = None
        self._pending_mic_countdown_seconds = 0
        self.hide_typing_bubble()
        self.status_overlay.show_warning("语音识别结果已过期，未发送", hide_after_ms=1800)

    def _clear_pending_microphone_text(self) -> None:
        if self._pending_mic_job is not None:
            self.root.after_cancel(self._pending_mic_job)
            self._pending_mic_job = None
        self._pending_mic_text = ""
        self._pending_mic_started_at = None
        self._pending_mic_countdown_seconds = 0

    def _consume_pending_microphone_text(self) -> str:
        text = self._pending_mic_text
        if text:
            self._clear_pending_microphone_text()
            self.status_overlay.show_done("已填入语音识别结果", hide_after_ms=1200)
        return text

    def quit(self) -> None:
        self.hide_typing_bubble()
        self.stop_microphone_listener()
        self._clear_pending_microphone_text()
        self.input_hotkey_manager.unregister()
        self.microphone_hotkey_manager.unregister()
        self.tray.stop()
        self.root.after(0, self.root.destroy)


if __name__ == "__main__":
    Application().start()
