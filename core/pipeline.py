from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from core.config import ConfigManager
from core.errors import ErrorHandler
from services.audio_player import play_audio_to_virtual_mic
from services.osc_client import VrcOscClient
from services.translator import translate_text
from services.tts_client import synthesize_tts


class AppPipeline:
    TOTAL_STEPS = 6

    def __init__(
        self,
        config_manager: ConfigManager,
        error_handler: ErrorHandler,
        progress_callback: Callable[[int, int, str], None] | None = None,
        done_callback: Callable[[str], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
        before_audio_callback: Callable[[], None] | None = None,
        finish_callback: Callable[[], None] | None = None,
    ):
        self.config_manager = config_manager
        self.error_handler = error_handler
        self.progress_callback = progress_callback
        self.done_callback = done_callback
        self.error_callback = error_callback
        self.before_audio_callback = before_audio_callback
        self.finish_callback = finish_callback
        self._lock = threading.Lock()

    def submit(self, original_text: str) -> None:
        text = original_text.strip()
        if not text:
            return
        thread = threading.Thread(target=self._run, args=(text,), daemon=True)
        thread.start()

    def _run(self, original_text: str) -> None:
        if not self._lock.acquire(blocking=False):
            message = "上一条 TTS 任务还没有完成，请稍后再试"
            self.error_handler.report("任务忙碌", message)
            self._notify_error(self.error_handler.short_message(message))
            return

        config = self.config_manager.get()
        osc = VrcOscClient(config)
        audio_path: Path | None = None
        voice_opened = False
        try:
            self._notify_progress(1, "正在翻译为日文...")
            translated = translate_text(original_text, config)

            self._notify_progress(2, "正在调用 OpenAI TTS...")
            audio_path = synthesize_tts(translated, config)
            bubble = config.bubble_format.format(original=original_text, translated=translated)

            self._notify_progress(3, "正在发送 VRChat 聊天气泡...")
            osc.send_chatbox(bubble)

            self._notify_progress(4, "正在开启 VRChat 麦克风...")
            osc.set_voice(True)
            voice_opened = True

            self._notify_before_audio()
            self._notify_progress(5, "正在播放 TTS 音频...")
            play_audio_to_virtual_mic(audio_path, config)

            self._notify_progress(6, "正在关闭 VRChat 麦克风...")
            osc.set_voice(False)
            voice_opened = False
            self._notify_done("发送完成")
        except Exception as exc:
            self.error_handler.report("VRC TTS 流程失败", exc)
            if voice_opened:
                try:
                    osc.set_voice(False)
                except Exception:
                    pass
            self._notify_error(self.error_handler.short_message(exc))
        finally:
            if audio_path is not None:
                try:
                    audio_path.unlink(missing_ok=True)
                except Exception:
                    pass
            self._lock.release()
            self._notify_finish()

    def _notify_progress(self, step: int, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(step, self.TOTAL_STEPS, message)

    def _notify_done(self, message: str) -> None:
        if self.done_callback is not None:
            self.done_callback(message)

    def _notify_error(self, message: str) -> None:
        if self.error_callback is not None:
            self.error_callback(message)

    def _notify_before_audio(self) -> None:
        if self.before_audio_callback is not None:
            self.before_audio_callback()

    def _notify_finish(self) -> None:
        if self.finish_callback is not None:
            self.finish_callback()
