from __future__ import annotations

import os
from collections import deque

import numpy as np

from core.errors import AppError

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512  # 32ms @ 16kHz，Silero VAD 的固定窗口大小
_CONTEXT_SAMPLES = 64
_WINDOW_MS = WINDOW_SAMPLES * 1000.0 / SAMPLE_RATE


class StreamingSileroVad:
    """基于 faster-whisper 自带的 Silero VAD v6 ONNX 模型的流式封装。

    每次喂入一个 512 采样（32ms @ 16kHz）的窗口，返回该窗口的语音概率，
    内部维护 LSTM 状态与上一窗口的 64 采样上下文。
    """

    def __init__(self):
        try:
            import onnxruntime
            from faster_whisper.utils import get_assets_path
        except Exception as exc:
            raise AppError("加载 Silero VAD 失败：请确认已安装 faster-whisper>=1.2 与 onnxruntime") from exc

        model_path = os.path.join(get_assets_path(), "silero_vad_v6.onnx")
        if not os.path.exists(model_path):
            raise AppError("未找到 faster-whisper 自带的 silero_vad_v6.onnx，请升级 faster-whisper>=1.2")

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(
            model_path,
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, _CONTEXT_SAMPLES), dtype=np.float32)

    def probability(self, window: np.ndarray) -> float:
        window = np.asarray(window, dtype=np.float32).reshape(-1)
        if window.shape[0] < WINDOW_SAMPLES:
            window = np.pad(window, (0, WINDOW_SAMPLES - window.shape[0]))
        window = window[:WINDOW_SAMPLES].reshape(1, -1)
        frame = np.concatenate([self._context, window], axis=1)
        out, self._h, self._c = self._session.run(None, {"input": frame, "h": self._h, "c": self._c})
        self._context = frame[:, -_CONTEXT_SAMPLES:]
        return float(np.asarray(out).reshape(-1)[0])


class VadSegmenter:
    """把连续音频窗口流切分成完整的语音片段。

    状态机仿照 Silero get_speech_timestamps：概率高于 threshold 触发语音，
    低于 neg_threshold 开始累计静音，静音超过 min_silence_ms 结束片段；
    片段前后各保留 speech_pad_ms 的余量。超过 max_speech_seconds 后优先等一个
    split_silence_ms 的短停顿再切分，避免在词中间硬切伤害识别准确度；
    迟迟等不到停顿则在 1.5 倍 max_speech_seconds 处强制切分兜底。
    """

    def __init__(
        self,
        vad: StreamingSileroVad,
        threshold: float = 0.5,
        min_speech_ms: int = 300,
        min_silence_ms: int = 900,
        speech_pad_ms: int = 240,
        max_speech_seconds: float = 8.0,
        split_silence_ms: int = 200,
    ):
        self._vad = vad
        self.threshold = min(max(float(threshold), 0.05), 0.95)
        self.neg_threshold = max(self.threshold - 0.15, 0.01)
        self._min_speech_windows = max(1, int(min_speech_ms / _WINDOW_MS))
        self._min_silence_windows = max(1, int(min_silence_ms / _WINDOW_MS))
        self._pad_windows = max(1, int(speech_pad_ms / _WINDOW_MS))
        self._max_windows = max(self._min_speech_windows + 1, int(max_speech_seconds * 1000 / _WINDOW_MS))
        self._split_silence_windows = max(1, int(split_silence_ms / _WINDOW_MS))
        self._hard_max_windows = int(self._max_windows * 1.5)
        self._pre_buffer: deque[np.ndarray] = deque(maxlen=self._pad_windows)
        self._segment: list[np.ndarray] = []
        self._silence_windows = 0
        self._voiced_windows = 0
        self.triggered = False
        self.last_probability = 0.0

    def reset(self) -> None:
        self._vad.reset()
        self._pre_buffer.clear()
        self._segment = []
        self._silence_windows = 0
        self._voiced_windows = 0
        self.triggered = False
        self.last_probability = 0.0

    def feed(self, window: np.ndarray) -> list[np.ndarray]:
        """喂入一个 512 采样窗口，返回本次新完成的语音片段列表。"""
        window = np.asarray(window, dtype=np.float32).reshape(-1)
        probability = self._vad.probability(window)
        self.last_probability = probability
        completed: list[np.ndarray] = []

        if not self.triggered:
            if probability >= self.threshold:
                self.triggered = True
                self._segment = list(self._pre_buffer) + [window]
                self._pre_buffer.clear()
                self._silence_windows = 0
                self._voiced_windows = 1
            else:
                self._pre_buffer.append(window)
            return completed

        self._segment.append(window)
        if probability >= self.threshold:
            self._silence_windows = 0
            self._voiced_windows += 1
        elif probability < self.neg_threshold or self._silence_windows > 0:
            self._silence_windows += 1

        if self._silence_windows >= self._min_silence_windows:
            segment = self._finalize_segment()
            if segment is not None:
                completed.append(segment)
            self.triggered = False
            self._segment = []
            self._silence_windows = 0
            self._voiced_windows = 0
        elif len(self._segment) >= self._max_windows and (
            self._silence_windows >= self._split_silence_windows
            or len(self._segment) >= self._hard_max_windows
        ):
            # 说话太长，在短停顿处切分（或到硬上限强制切），保持触发状态继续收集后续语音
            completed.append(np.concatenate(self._segment))
            self._segment = []
            self._voiced_windows = 1
            self._silence_windows = 0

        return completed

    def flush(self) -> np.ndarray | None:
        """停止采集时把未完结的片段取出。"""
        if not self.triggered:
            return None
        segment = self._finalize_segment()
        self.triggered = False
        self._segment = []
        self._silence_windows = 0
        self._voiced_windows = 0
        return segment

    def _finalize_segment(self) -> np.ndarray | None:
        if self._voiced_windows < self._min_speech_windows or not self._segment:
            return None
        # 片段尾部带着整段确认静音，裁掉超过 pad 的部分
        extra_silence = self._silence_windows - self._pad_windows
        if extra_silence > 0:
            self._segment = self._segment[: len(self._segment) - extra_silence]
        if not self._segment:
            return None
        return np.concatenate(self._segment)
