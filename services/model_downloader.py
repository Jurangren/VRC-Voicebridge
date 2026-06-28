"""按需下载本地模型（声纹模型 / 本地 Whisper 模型），带进度，供 Web 页面的下载按钮使用。

发布版 exe 不内置模型权重——代码与 native 库已打包，模型在用户点击下载时才拉取：
- 声纹模型：约 28MB，下载到 models/（见 services/speaker_cluster.ensure_speaker_model）；
- 本地 Whisper：按所选型号从 Hugging Face 下到 HF 缓存，之后 faster-whisper 直接离线加载。

后台线程下载，主线程/前端通过 status() 轮询进度。
"""
from __future__ import annotations

import threading

from core.config import AppConfig


class ModelDownloader:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"running": False, "done": False, "error": "", "percent": 0, "message": "未开始", "target": ""}

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _set(self, **kwargs) -> None:
        with self._lock:
            self._state.update(kwargs)

    def start(self, target: str, config: AppConfig) -> bool:
        with self._lock:
            if self._state["running"]:
                return False
            self._state = {"running": True, "done": False, "error": "", "percent": 0,
                           "message": "准备下载...", "target": target}
        threading.Thread(target=self._run, args=(target, config), daemon=True).start()
        return True

    def _run(self, target: str, config: AppConfig) -> None:
        try:
            if target == "speaker":
                self._download_speaker()
            elif target == "whisper":
                self._download_whisper(config)
            else:
                raise ValueError(f"未知下载目标：{target}")
            self._set(running=False, done=True, percent=100, message="下载完成")
        except Exception as exc:
            self._set(running=False, done=False, error=str(exc), message=f"下载失败：{exc}")

    # ---------- 声纹模型 ----------

    def _download_speaker(self) -> None:
        from services.speaker_cluster import ensure_speaker_model

        def progress(percent, message):
            if percent is None:
                self._set(message=message)
            else:
                self._set(percent=int(percent), message=message)

        ensure_speaker_model("", progress)

    # ---------- 本地 Whisper 模型 ----------

    def _download_whisper(self, config: AppConfig) -> None:
        name = (config.speech_translate_local_whisper_model or "").strip() or "large-v3-turbo"
        self._set(percent=0, message=f"正在下载本地 Whisper 模型：{name} ...")
        repo = self._whisper_repo(name)
        if repo is not None:
            from huggingface_hub import snapshot_download

            tqdm_class = self._make_tqdm()
            if tqdm_class is not None:
                snapshot_download(repo_id=repo, tqdm_class=tqdm_class)
            else:
                snapshot_download(repo_id=repo)
        else:
            # 兜底：型号不在已知映射表里，用 faster-whisper 自带下载（无字节级进度）
            from faster_whisper import download_model

            self._set(message=f"正在下载本地 Whisper 模型：{name}（无精确进度，请耐心等待）...")
            download_model(name)
        self._set(percent=100, message=f"Whisper 模型 {name} 下载完成")

    def _whisper_repo(self, name: str) -> str | None:
        try:
            from faster_whisper.utils import _MODELS  # 型号短名 -> Hugging Face 仓库

            return _MODELS.get(name)
        except Exception:
            return None

    def _make_tqdm(self):
        try:
            from tqdm.auto import tqdm as base_tqdm
        except Exception:
            return None

        downloader = self

        class _ProgressTqdm(base_tqdm):
            # 只取按字节计的下载条（unit == "B"）的进度，忽略 huggingface_hub 的"已取 N 个文件"计数条
            def update(self, n=1):
                ret = super().update(n)
                try:
                    if getattr(self, "unit", "") == "B" and self.total:
                        downloader._set(percent=min(max(int(self.n * 100 / self.total), 0), 100))
                except Exception:
                    pass
                return ret

        return _ProgressTqdm


MODEL_DOWNLOADER = ModelDownloader()
