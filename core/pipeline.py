from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from core.config import ConfigManager
from core.errors import ErrorHandler
from services.audio_player import play_audio_to_virtual_mic
from services.osc_client import VrcOscClient
from services.translator import translate_text
from services.tts_client import synthesize_tts


class PipelineCancelled(Exception):
    pass


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
        self._state_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self._can_cancel_before_audio = False

    def submit(self, original_text: str, started_at: float | None = None) -> None:
        text = original_text.strip()
        if not text:
            return
        if started_at is None:
            started_at = time.perf_counter()
        thread = threading.Thread(target=self._run, args=(text, started_at), daemon=True)
        thread.start()

    def cancel_before_audio(self) -> bool:
        with self._state_lock:
            if not self._can_cancel_before_audio:
                return False
            # 已经在取消中（TTS 线程可能还阻塞在 synthesize_tts 里没来得及处理）：
            # 不要再当成一次新取消，返回 False 让调用方直接去开下一轮录音，而不是重复弹"已取消"。
            if self._cancel_event.is_set():
                return False
            self._cancel_event.set()
            return True

    def _run(self, original_text: str, started_at: float) -> None:
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
            self._cancel_event.clear()
            self._set_can_cancel_before_audio(False)
            self._notify_progress(1, "正在翻译为日文...")
            translation_started_at = time.perf_counter()
            translated = translate_text(original_text, config)
            translation_seconds = time.perf_counter() - translation_started_at

            self._set_can_cancel_before_audio(True)
            self._raise_if_cancelled()
            self._notify_progress(2, "正在调用 OpenAI TTS...")
            tts_started_at = time.perf_counter()
            audio_path = synthesize_tts(translated, config)
            tts_seconds = time.perf_counter() - tts_started_at
            self._raise_if_cancelled()
            bubble = config.bubble_format.format(original=original_text, translated=translated)

            self._notify_progress(3, "正在发送 VRChat 聊天气泡...")
            self._raise_if_cancelled()
            osc.send_chatbox(bubble)

            self._notify_progress(4, "正在开启 VRChat 麦克风...")
            self._raise_if_cancelled()
            osc.set_voice(True)
            voice_opened = True

            self._raise_if_cancelled()
            self._set_can_cancel_before_audio(False)
            self._notify_before_audio()
            total_seconds = time.perf_counter() - started_at
            self._notify_progress(5, "正在播放 TTS 音频...")
            play_audio_to_virtual_mic(audio_path, config)

            self._notify_progress(6, "正在关闭 VRChat 麦克风...")
            osc.set_voice(False)
            voice_opened = False
            self._notify_done(
                f"翻译耗时: {translation_seconds:.2f}秒"
                f"\n生成耗时: {tts_seconds:.2f}秒"
                f"\n总耗时: {total_seconds:.2f}秒"
            )
        except PipelineCancelled:
            self.error_handler.report("TTS 流程已取消", "已通过语音热键取消当前 TTS 操作")
            if voice_opened:
                try:
                    osc.set_voice(False)
                except Exception:
                    pass
            # 取消浮窗已由按下热键的处理函数即时显示；这里不再迟到地重弹一次，
            # 否则会（在 synthesize_tts 返回后）盖掉用户已经开始的下一轮录音浮窗。
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
            self._cancel_event.clear()
            self._set_can_cancel_before_audio(False)
            self._notify_finish()

    def _set_can_cancel_before_audio(self, enabled: bool) -> None:
        with self._state_lock:
            self._can_cancel_before_audio = enabled

    def _raise_if_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise PipelineCancelled()

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
