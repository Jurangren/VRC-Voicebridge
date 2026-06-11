from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import numpy as np

from core.errors import AppError

DEFAULT_MODEL_NAME = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
# 按可达性排序的候选下载源（hf-mirror 在国内网络下最稳定）
DEFAULT_MODEL_URLS = [
    "https://hf-mirror.com/csukuangfj/speaker-embedding-models/resolve/main/" + DEFAULT_MODEL_NAME,
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/" + DEFAULT_MODEL_NAME,
    "https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/" + DEFAULT_MODEL_NAME,
]
MODELS_DIR = Path("models")
# 声纹嵌入超过这个长度只取前面部分，避免超长片段拖慢嵌入计算
MAX_EMBED_SECONDS = 10.0


class SpeakerEmbedder:
    """sherpa-onnx 声纹嵌入提取器（CAM++ 中英通用模型，CPU 推理）。"""

    def __init__(self, model_path: str = "", status_callback: Callable[[str], None] | None = None):
        try:
            import sherpa_onnx
        except Exception as exc:
            raise AppError("声纹聚类失败：请先安装 sherpa-onnx（pip install sherpa-onnx）") from exc

        path = self._resolve_model_path(model_path, status_callback or (lambda _msg: None))
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(path), num_threads=2, provider="cpu")
        if not config.validate():
            raise AppError(f"声纹模型无效：{path}")
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.model_path = str(path)

    def embed(self, samples: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        max_samples = int(MAX_EMBED_SECONDS * sample_rate)
        if samples.shape[0] > max_samples:
            samples = samples[:max_samples]
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
        stream.input_finished()
        embedding = np.asarray(self._extractor.compute(stream), dtype=np.float32)
        norm = float(np.linalg.norm(embedding))
        return embedding / norm if norm > 0 else embedding

    @staticmethod
    def _resolve_model_path(model_path: str, report: Callable[[str], None]) -> Path:
        if model_path.strip():
            path = Path(model_path.strip())
            if not path.exists():
                raise AppError(f"声纹模型路径不存在：{path}")
            return path

        path = MODELS_DIR / DEFAULT_MODEL_NAME
        if path.exists():
            return path

        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        part_path = path.with_suffix(".part")
        report("首次使用声纹聚类，正在下载声纹模型（约 28MB）...")
        import requests

        last_error: Exception | None = None
        for url in DEFAULT_MODEL_URLS:
            try:
                with requests.get(url, stream=True, timeout=(10, 600)) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("Content-Length", 0))
                    downloaded = 0
                    with part_path.open("wb") as file:
                        for chunk in response.iter_content(chunk_size=1024 * 256):
                            file.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                report(f"正在下载声纹模型... {downloaded * 100 // total}%")
                part_path.replace(path)
                report("声纹模型下载完成")
                return path
            except Exception as exc:
                part_path.unlink(missing_ok=True)
                last_error = exc
                report(f"下载源不可用，尝试下一个... {exc}")
        raise AppError(
            "声纹模型下载失败："
            f"{last_error}\n"
            f"可手动下载 {DEFAULT_MODEL_URLS[0]} 保存到 {path}，"
            "或在页面“声纹模型路径”里填写已下载的模型文件路径。"
        ) from last_error


class OnlineSpeakerClusterer:
    """在线声纹聚类：余弦相似度最近邻 + 质心增量更新。

    相似度达到阈值归入已有说话人，否则新建；超过最大说话人数后强制归入最相近的一类。
    """

    def __init__(self, similarity_threshold: float = 0.6, max_speakers: int = 6):
        self.similarity_threshold = min(max(float(similarity_threshold), 0.1), 0.95)
        self.max_speakers = max(1, int(max_speakers))
        self._centroids: list[np.ndarray] = []
        self._counts: list[int] = []
        self._lock = threading.Lock()

    def assign(self, embedding: np.ndarray) -> int:
        """返回 1 开始的说话人编号。"""
        with self._lock:
            if not self._centroids:
                self._centroids.append(embedding)
                self._counts.append(1)
                return 1
            similarities = [float(np.dot(centroid, embedding)) for centroid in self._centroids]
            best = int(np.argmax(similarities))
            if similarities[best] >= self.similarity_threshold or len(self._centroids) >= self.max_speakers:
                count = min(self._counts[best], 50)  # 限制权重，让质心保持一定适应性
                centroid = self._centroids[best] * count + embedding
                norm = float(np.linalg.norm(centroid))
                self._centroids[best] = centroid / norm if norm > 0 else centroid
                self._counts[best] += 1
                return best + 1
            self._centroids.append(embedding)
            self._counts.append(1)
            return len(self._centroids)

    @property
    def speaker_count(self) -> int:
        with self._lock:
            return len(self._centroids)

    def reset(self) -> None:
        with self._lock:
            self._centroids = []
            self._counts = []
