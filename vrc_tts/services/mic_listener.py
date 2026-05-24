from __future__ import annotations

import threading
import time
from typing import Callable

import speech_recognition as sr

from vrc_tts.core.config import AppConfig
from vrc_tts.core.errors import AppError


class MicrophoneListener:
    def __init__(
        self,
        config: AppConfig,
        text_callback: Callable[[str], None],
        error_callback: Callable[[Exception | str], None],
        finish_callback: Callable[[bool], None] | None = None,
    ):
        self.config = config
        self.text_callback = text_callback
        self.error_callback = error_callback
        self.finish_callback = finish_callback
        self._stop_event = threading.Event()
        self._capture_stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture_thread: threading.Thread | None = None
        self._capture_lock = threading.Lock()
        self._is_capturing = False

    def start(self) -> None:
        self._stop_event.clear()

    def stop(self) -> None:
        self._stop_event.set()
        self.stop_capture()

    def start_capture(self) -> bool:
        with self._capture_lock:
            if self._is_capturing:
                return False
            self._is_capturing = True
            self._capture_stop_event.clear()
            self._capture_thread = threading.Thread(target=self._capture_once, daemon=True)
            self._capture_thread.start()
            return True

    def stop_capture(self) -> None:
        self._capture_stop_event.set()

    def _capture_once(self) -> None:
        recognizer = sr.Recognizer()
        recognizer.energy_threshold = max(1, int(self.config.listen_energy_threshold))
        recognizer.dynamic_energy_threshold = False
        frames: list[bytes] = []
        sample_rate = 16000
        sample_width = 2
        max_seconds = max(1, int(self.config.listen_phrase_time_limit))
        recognized = False

        try:
            device_index = self.config.listen_mic_device_index if self.config.listen_mic_device_index >= 0 else None
            with sr.Microphone(device_index=device_index) as source:
                sample_rate = source.SAMPLE_RATE
                sample_width = source.SAMPLE_WIDTH
                deadline = time.monotonic() + max_seconds
                while not self._capture_stop_event.is_set() and time.monotonic() < deadline:
                    frames.append(source.stream.read(source.CHUNK))

            if not frames or self._stop_event.is_set():
                return

            audio = sr.AudioData(b"".join(frames), sample_rate, sample_width)
            text = recognizer.recognize_google(audio, language=self.config.listen_language).strip()
            if text:
                recognized = True
                self.text_callback(text)
        except sr.UnknownValueError:
            pass
        except sr.RequestError as exc:
            self.error_callback(AppError(f"麦克风语音识别请求失败：{exc}"))
        except Exception as exc:
            self.error_callback(AppError(f"麦克风按键录音失败：{exc}"))
        finally:
            with self._capture_lock:
                self._is_capturing = False
                self._capture_stop_event.clear()
            if self.finish_callback is not None:
                self.finish_callback(recognized)
