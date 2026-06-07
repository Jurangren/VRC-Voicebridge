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


def recognize_with_local_whisper_gpu(audio: sr.AudioData, config: AppConfig) -> str:
    model_name = config.speech_translate_local_whisper_model.strip() or "large-v3-turbo"
    model = _get_model(model_name)
    language = (config.speech_translate_recognition_language or config.speech_translate_source_language).split("-")[0]
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
