from __future__ import annotations

import shutil
import subprocess
import tempfile
import os
import site
from pathlib import Path

import speech_recognition as sr

from core.config import AppConfig
from core.errors import AppError

_MODEL = None
_MODEL_NAME = ""
_DLL_DIRECTORY_HANDLES = []


def ensure_local_whisper_model(config: AppConfig) -> None:
    """预加载模型（实时管线启动时调用，避免第一句话才开始加载）。"""
    model_name = config.speech_translate_local_whisper_model.strip() or "large-v3-turbo"
    _get_model(model_name)


def transcribe_samples_with_local_whisper(samples, config: AppConfig) -> str:
    """直接对 16kHz float32 numpy 音频做转写，不经过临时 WAV 文件。"""
    import numpy as np

    model_name = config.speech_translate_local_whisper_model.strip() or "large-v3-turbo"
    model = _get_model(model_name)
    language = (config.speech_translate_recognition_language or config.speech_translate_source_language).split("-")[0]
    segments, _info = model.transcribe(
        np.asarray(samples, dtype=np.float32).reshape(-1),
        language=language or None,
        vad_filter=False,
        beam_size=5,
        temperature=0.0,
        condition_on_previous_text=False,
        repetition_penalty=1.1,
        no_repeat_ngram_size=3,
    )
    texts = []
    for segment in segments:
        # 只用单一 temperature 时 faster-whisper 没有回退重试，质量差的段会原样保留，
        # 需要自行丢弃疑似幻觉段：近乎静音、置信度极低或内部高度重复（高压缩比）
        if segment.no_speech_prob > 0.85:
            continue
        if segment.avg_logprob < -1.2:
            continue
        if segment.compression_ratio > 2.4:
            continue
        texts.append(segment.text)
    return "".join(texts).strip()


def recognize_with_local_whisper_gpu(audio: sr.AudioData, config: AppConfig) -> str:
    model_name = config.speech_translate_local_whisper_model.strip() or "large-v3-turbo"
    model = _get_model(model_name)
    # 此函数服务于麦克风输入路径（按键录音/VAD 监听/旧输出采集），识别语言取 listen_language；
    # 旧的输出采集端点会把 listen_language 覆盖为实时翻译源语言，因此两条路径都正确。
    language = (config.listen_language or config.speech_translate_recognition_language).split("-")[0]
    wav_data = audio.get_wav_data(convert_rate=16000, convert_width=2)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_file.write(wav_data)
            temp_path = Path(temp_file.name)
        segments, _info = model.transcribe(
            str(temp_path),
            language=language or None,
            vad_filter=False,
            beam_size=5,
            temperature=0.0,
        )
        text = "".join(segment.text for segment in segments).strip()
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    if not text:
        raise sr.UnknownValueError()
    return text


def _get_model(model_name: str):
    global _MODEL, _MODEL_NAME
    if _MODEL is not None and _MODEL_NAME == model_name:
        return _MODEL

    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise AppError("本地 Whisper 失败：请先安装 faster-whisper") from exc

    if not _has_nvidia_gpu():
        raise AppError("本地 Whisper 失败：未检测到 NVIDIA GPU。按要求本地推理不允许使用 CPU。")

    try:
        _add_nvidia_dll_directories()
        try:
            # 本地缓存已有模型时强制离线加载，跳过 Hugging Face 的联网版本检查/重新下载
            _MODEL = WhisperModel(model_name, device="cuda", compute_type="float16", local_files_only=True)
        except Exception:
            _MODEL = WhisperModel(model_name, device="cuda", compute_type="float16")
        _MODEL_NAME = model_name
        return _MODEL
    except Exception as exc:
        raise AppError(
            "本地 Whisper GPU 模型加载失败："
            f"{exc}\n"
            "如果这是首次使用，本程序需要先从 Hugging Face 下载模型；"
            "当前网络/证书/镜像异常会导致下载失败。"
            "也可以在页面的“本地 Whisper 模型”里填写已下载的 faster-whisper 模型目录路径。"
        ) from exc


def _has_nvidia_gpu() -> bool:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return False
    try:
        result = subprocess.run(
            [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _add_nvidia_dll_directories() -> None:
    if not hasattr(os, "add_dll_directory"):
        return
    global _DLL_DIRECTORY_HANDLES
    candidates: list[Path] = []
    for root in site.getsitepackages():
        nvidia_root = Path(root) / "nvidia"
        candidates.extend(
            [
                nvidia_root / "cublas" / "bin",
                nvidia_root / "cudnn" / "bin",
                nvidia_root / "cuda_nvrtc" / "bin",
            ]
        )
    for path in candidates:
        if path.exists():
            path_text = str(path)
            if path_text not in os.environ.get("PATH", ""):
                os.environ["PATH"] = path_text + os.pathsep + os.environ.get("PATH", "")
            handle = os.add_dll_directory(path_text)
            _DLL_DIRECTORY_HANDLES.append(handle)
