from __future__ import annotations

import queue
import sys
import threading
import warnings
from typing import Callable

import numpy as np
import soundcard as sc
from soundcard import SoundcardRuntimeWarning
import speech_recognition as sr

from core.config import AppConfig
from core.errors import AppError
from services.mic_listener import recognize_speech
from services.vad import SAMPLE_RATE, WINDOW_SAMPLES, StreamingSileroVad, VadSegmenter

_SEGMENT_QUEUE_LIMIT = 4


class VadMicListener:
    """麦克风 VAD 持续监听：检测到一句话后自动识别为文字，回调给待发送确认机制。

    与按键录音（MicrophoneListener）互斥使用：本模式下不需要按住热键录音，
    说话会被 VAD 自动分段识别，识别结果进入「N 秒内按热键发送」的确认流程。
    """

    def __init__(
        self,
        config: AppConfig,
        text_callback: Callable[[str], None],
        error_callback: Callable[[Exception | str], None],
    ):
        self.config = config
        self.text_callback = text_callback
        self.error_callback = error_callback
        self._stop_event = threading.Event()
        self._segment_queue: queue.Queue = queue.Queue(maxsize=_SEGMENT_QUEUE_LIMIT)
        self._threads: list[threading.Thread] = []
        self._speaking = False
        self._running = False

    def start(self) -> None:
        self._stop_event.clear()
        self._running = True
        capture = threading.Thread(target=self._capture_loop, daemon=True)
        worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._threads = [capture, worker]
        capture.start()
        worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False
        for thread in self._threads:
            thread.join(timeout=3)
        self._threads = []

    def status(self) -> dict:
        return {"enabled": self._running, "speaking": self._speaking}

    def _capture_loop(self) -> None:
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.ole32.CoInitializeEx(None, 0)
            except Exception:
                pass

        try:
            vad = StreamingSileroVad()
            segmenter = VadSegmenter(
                vad,
                threshold=self.config.mic_vad_threshold,
                min_speech_ms=250,
                min_silence_ms=self.config.mic_vad_silence_ms,
                max_speech_seconds=max(1, int(self.config.listen_phrase_time_limit)),
            )
            device_id = self.config.mic_vad_device_id.strip()
            microphone = sc.get_microphone(device_id) if device_id else sc.default_microphone()
        except Exception as exc:
            self._running = False
            self.error_callback(AppError(f"麦克风 VAD 监听初始化失败：{exc}"))
            return

        try:
            with microphone.recorder(samplerate=SAMPLE_RATE, channels=1) as recorder:
                while not self._stop_event.is_set():
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
                        block = np.asarray(recorder.record(numframes=WINDOW_SAMPLES), dtype=np.float32).reshape(-1)
                    if block.size == 0:
                        continue
                    for segment in segmenter.feed(block):
                        self._enqueue_segment(segment)
                    self._speaking = segmenter.triggered
        except Exception as exc:
            if not self._stop_event.is_set():
                self._running = False
                self.error_callback(AppError(f"麦克风 VAD 监听失败：{exc}"))
        finally:
            self._speaking = False

    def _enqueue_segment(self, segment: np.ndarray) -> None:
        while True:
            try:
                self._segment_queue.put_nowait(segment)
                return
            except queue.Full:
                try:
                    self._segment_queue.get_nowait()
                except queue.Empty:
                    pass

    def _worker_loop(self) -> None:
        if self.config.speech_recognition_provider == "local_whisper_gpu":
            try:
                from services.local_whisper import ensure_local_whisper_model

                ensure_local_whisper_model(self.config)
            except Exception as exc:
                self.error_callback(AppError(f"本地 Whisper 模型加载失败：{exc}"))
        recognizer = sr.Recognizer()
        while not self._stop_event.is_set():
            try:
                segment = self._segment_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                pcm = (np.clip(segment, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                audio = sr.AudioData(pcm, SAMPLE_RATE, 2)
                text = recognize_speech(audio, recognizer, self.config).strip()
                if text:
                    self.text_callback(text)
            except sr.UnknownValueError:
                continue
            except Exception as exc:
                self.error_callback(AppError(f"麦克风 VAD 语音识别失败：{exc}"))
