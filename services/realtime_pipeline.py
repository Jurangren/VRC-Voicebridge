from __future__ import annotations

import difflib
import queue
import re
import sys
import threading
import time
import warnings
from collections import deque
from datetime import datetime

import numpy as np
import soundcard as sc
from soundcard import SoundcardRuntimeWarning

from core.config import AppConfig
from services.local_whisper import ensure_local_whisper_model, transcribe_samples_with_local_whisper
from services.osc_client import VrcOscClient
from services.speaker_cluster import OnlineSpeakerClusterer, SpeakerEmbedder
from services.translator import translate_text
from services.vad import SAMPLE_RATE, WINDOW_SAMPLES, StreamingSileroVad, VadSegmenter

_EVENT_HISTORY_LIMIT = 200
_SEGMENT_QUEUE_LIMIT = 8
_DEDUP_HISTORY_LIMIT = 8
_DEDUP_WINDOW_SECONDS = 8.0
_DEDUP_SIMILARITY = 0.85

_PUNCTUATION_PATTERN = re.compile(r"[\s。，、！？．,.!?…~～\-—:：;；'\"“”‘’()（）\[\]【】]+")


def _normalize_for_dedup(text: str) -> str:
    return _PUNCTUATION_PATTERN.sub("", text).lower()

# 与前端 speech_translate.js 的 SPEAKER_COLORS 保持一致
SPEAKER_COLORS = ["#4f8cff", "#f59e0b", "#34d399", "#f472b6", "#a78bfa", "#22d3ee", "#fb7185", "#a3e635"]


def speaker_color(index: int) -> str:
    return SPEAKER_COLORS[(index - 1) % len(SPEAKER_COLORS)]


def speaker_letter(index: int) -> str:
    """把 1 开始的说话人编号转成 A-Z、AA-ZZ 形式的字母标签。"""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def list_microphone_devices() -> list[dict]:
    devices: list[dict] = []
    for index, microphone in enumerate(sc.all_microphones()):
        devices.append({"id": microphone.id, "index": index, "name": microphone.name})
    return devices


class RealtimeTranslatePipeline:
    """实时翻译管线：音频采集 -> Silero VAD 分段 -> 声纹聚类 -> 本地 faster-whisper -> 翻译 API -> 事件流。

    采集线程按 32ms 窗口读取音频喂给 VAD 分段器；完整语音片段进入队列，
    由工作线程依次做声纹归类、本地 Whisper 转写和翻译，结果追加到事件历史供前端轮询。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._segment_queue: queue.Queue = queue.Queue(maxsize=_SEGMENT_QUEUE_LIMIT)
        self._events: deque[dict] = deque(maxlen=_EVENT_HISTORY_LIMIT)
        self._event_id = 0
        self._config: AppConfig | None = None
        self._indicator = None
        self._embedder: SpeakerEmbedder | None = None
        self._clusterer = OnlineSpeakerClusterer()
        self._recent_texts: deque[tuple[str, float]] = deque(maxlen=_DEDUP_HISTORY_LIMIT)
        self._status: dict = {"running": False, "stage": "idle", "message": "未启动"}

    # ---------- 对外接口 ----------

    def start(self, config: AppConfig, indicator=None) -> None:
        with self._lock:
            self.stop()
            self._config = config
            self._indicator = indicator
            self._stop_event.clear()
            self._segment_queue = queue.Queue(maxsize=_SEGMENT_QUEUE_LIMIT)
            self._clusterer = OnlineSpeakerClusterer(
                similarity_threshold=config.speech_translate_speaker_similarity,
                max_speakers=config.speech_translate_max_speakers,
            )
            self._recent_texts.clear()
            self._status = {
                "running": True,
                "stage": "loading",
                "message": "正在加载模型...",
                "speaking": False,
                "rms": 0.0,
                "vad_probability": 0.0,
                "queue_size": 0,
                "speaker_count": 0,
                "last_error": "",
            }
            ready_event = threading.Event()
            worker = threading.Thread(target=self._worker_loop, args=(config, ready_event), daemon=True)
            capture = threading.Thread(target=self._capture_loop, args=(config, ready_event), daemon=True)
            self._threads = [worker, capture]
            worker.start()
            capture.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            threads = self._threads
            self._threads = []
        for thread in threads:
            thread.join(timeout=5)
        with self._lock:
            self._status.update({"running": False, "stage": "idle", "message": "已停止", "speaking": False})

    def status(self) -> dict:
        with self._lock:
            snapshot = dict(self._status)
            snapshot["queue_size"] = self._segment_queue.qsize()
            snapshot["speaker_count"] = self._clusterer.speaker_count
            return snapshot

    def events_after(self, after_id: int) -> list[dict]:
        with self._lock:
            return [event for event in self._events if event["id"] > after_id]

    # ---------- 内部工具 ----------

    def _set_status(self, **kwargs) -> None:
        with self._lock:
            self._status.update(kwargs)

    def _fail(self, message: str) -> None:
        self._stop_event.set()
        self._set_status(running=False, stage="error", message=message, last_error=message, speaking=False)

    def _emit_event(self, event: dict) -> None:
        with self._lock:
            self._event_id += 1
            event["id"] = self._event_id
            self._events.append(event)

    def _is_duplicate_text(self, text: str, now: float) -> bool:
        """近似去重：与时间窗内的近期转写做相似度比较，挡住标点/个别字差异的重复。"""
        normalized = _normalize_for_dedup(text)
        if not normalized:
            return True
        for prev_text, prev_at in self._recent_texts:
            if now - prev_at > _DEDUP_WINDOW_SECONDS:
                continue
            if normalized == prev_text:
                return True
            if difflib.SequenceMatcher(None, normalized, prev_text).ratio() >= _DEDUP_SIMILARITY:
                return True
        return False

    # ---------- 采集线程：音频 -> VAD -> 片段队列 ----------

    def _capture_loop(self, config: AppConfig, ready_event: threading.Event) -> None:
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.ole32.CoInitializeEx(None, 0)
            except Exception:
                pass

        # 等模型加载完成再开始采集，避免片段在队列里堆积过期
        while not ready_event.wait(timeout=0.2):
            if self._stop_event.is_set():
                return

        try:
            vad = StreamingSileroVad()
            segmenter = VadSegmenter(
                vad,
                threshold=config.speech_translate_vad_threshold,
                min_speech_ms=config.speech_translate_min_speech_ms,
                min_silence_ms=config.speech_translate_silence_ms,
                max_speech_seconds=config.speech_translate_chunk_seconds,
            )
        except Exception as exc:
            self._fail(f"VAD 初始化失败：{exc}")
            return

        try:
            if config.speech_translate_audio_source == "output":
                device_id = config.speech_translate_output_device_id.strip()
                speaker = sc.get_speaker(device_id) if device_id else sc.default_speaker()
                microphone = sc.get_microphone(speaker.id, include_loopback=True)
                source_name = f"系统输出回环（{speaker.name}）"
            else:
                device_id = config.speech_translate_mic_device_id.strip()
                microphone = sc.get_microphone(device_id) if device_id else sc.default_microphone()
                source_name = f"麦克风（{microphone.name}）"
        except Exception as exc:
            self._fail(f"打开音频设备失败：{exc}")
            return

        self._set_status(stage="listening", message=f"正在监听 {source_name}")
        try:
            with microphone.recorder(samplerate=SAMPLE_RATE, channels=1) as recorder:
                while not self._stop_event.is_set():
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", category=SoundcardRuntimeWarning)
                        block = np.asarray(recorder.record(numframes=WINDOW_SAMPLES), dtype=np.float32).reshape(-1)
                    if block.size == 0:
                        continue
                    rms = float(np.sqrt(np.mean(np.square(block))))
                    for segment in segmenter.feed(block):
                        self._enqueue_segment(segment)
                    self._set_status(
                        speaking=segmenter.triggered,
                        rms=rms,
                        vad_probability=round(segmenter.last_probability, 3),
                    )
                tail = segmenter.flush()
                if tail is not None:
                    self._enqueue_segment(tail)
        except Exception as exc:
            if not self._stop_event.is_set():
                self._fail(f"音频采集失败：{exc}")

    def _enqueue_segment(self, segment: np.ndarray) -> None:
        while True:
            try:
                self._segment_queue.put_nowait(segment)
                return
            except queue.Full:
                try:
                    self._segment_queue.get_nowait()  # 处理不过来时丢弃最旧片段
                except queue.Empty:
                    pass

    # ---------- 工作线程：片段 -> 声纹 -> Whisper -> 翻译 -> 事件 ----------

    def _worker_loop(self, config: AppConfig, ready_event: threading.Event) -> None:
        try:
            self._set_status(stage="loading", message="正在加载本地 Whisper 模型...")
            ensure_local_whisper_model(config)
        except Exception as exc:
            self._fail(str(exc))
            return

        self._embedder = None
        if config.speech_translate_speaker_enabled:
            try:
                self._set_status(stage="loading", message="正在加载声纹模型...")
                self._embedder = SpeakerEmbedder(
                    config.speech_translate_speaker_model_path,
                    status_callback=lambda message: self._set_status(message=message),
                )
            except Exception as exc:
                # 声纹模型加载失败不阻断管线，降级为单说话人
                self._set_status(last_error=f"声纹聚类不可用，已降级为单说话人：{exc}")

        ready_event.set()
        while not self._stop_event.is_set():
            try:
                segment = self._segment_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process_segment(segment, config)
            except Exception as exc:
                self._set_status(last_error=f"片段处理失败：{exc}")

    def _process_segment(self, segment: np.ndarray, config: AppConfig) -> None:
        speaker = 0
        if self._embedder is not None:
            try:
                speaker = self._clusterer.assign(self._embedder.embed(segment, SAMPLE_RATE))
            except Exception as exc:
                self._set_status(last_error=f"声纹归类失败：{exc}")

        original = transcribe_samples_with_local_whisper(segment, config)
        if not original:
            return
        now = time.monotonic()
        if self._is_duplicate_text(original, now):
            return  # 过滤 Whisper 对静音/切碎片段的幻觉式重复输出
        self._recent_texts.append((_normalize_for_dedup(original), now))

        translated = ""
        translate_error = ""
        try:
            translated = translate_text(original, config)
        except Exception as exc:
            translate_error = str(exc)
            self._set_status(last_error=f"翻译失败：{exc}")

        label = speaker_letter(speaker) if speaker else ""
        if translated and config.speech_translate_osc_enabled:
            try:
                message = (
                    config.speech_translate_osc_format
                    .replace("{original}", original)
                    .replace("{translated}", translated)
                    .replace("{translation}", translated)
                    .replace("{speaker}", label)
                ).strip()
                if message:
                    VrcOscClient(config).send_listen_chatbox(
                        message, config.speech_translate_osc_user_hold_seconds
                    )
            except Exception as exc:
                self._set_status(last_error=f"收听翻译聊天框显示失败：{exc}")
        if translated and self._indicator is not None:
            try:
                self._indicator.show_text(
                    translated,
                    float(config.speech_translate_overlay_text_seconds),
                    float(config.speech_translate_overlay_text_alpha),
                    speaker_label=label,
                    speaker_color=speaker_color(speaker) if speaker else "",
                )
            except Exception:
                pass

        self._emit_event(
            {
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "speaker": speaker,
                "speaker_label": label,
                "original": original,
                "translated": translated,
                "error": translate_error,
                "duration": round(segment.shape[0] / SAMPLE_RATE, 2),
                "provider": config.translation_provider,
                "source_language": config.source_language,
                "target_language": config.target_language,
            }
        )


PIPELINE = RealtimeTranslatePipeline()
