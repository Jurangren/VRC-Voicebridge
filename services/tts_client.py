from __future__ import annotations

import tempfile
import time
from pathlib import Path

from openai import OpenAI

from core.config import AppConfig
from core.errors import AppError


def synthesize_tts(text: str, config: AppConfig) -> Path:
    if not config.openai_api_key:
        raise AppError("OpenAI API Key 为空，请先在设置面板填写")

    attempts = max(1, int(config.tts_retry_count) + 1)
    last_error: Exception | None = None
    suffix = f".{config.openai_tts_format or 'wav'}"

    for attempt in range(1, attempts + 1):
        try:
            client = OpenAI(
                api_key=config.openai_api_key,
                base_url=config.openai_base_url or None,
                timeout=config.tts_timeout_seconds,
            )
            response = client.audio.speech.create(
                model=config.openai_tts_model,
                voice=config.openai_tts_voice,
                input=text,
                response_format=config.openai_tts_format,
            )

            output_path = Path(tempfile.gettempdir()) / f"_{int(time.time() * 1000)}{suffix}"
            if hasattr(response, "write_to_file"):
                response.write_to_file(str(output_path))
            else:
                content = getattr(response, "content", None)
                if content is None:
                    content = response.read()
                output_path.write_bytes(content)
            return output_path
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(0.8 * attempt)

    raise AppError(f"OpenAI TTS 失败，已尝试 {attempts} 次：{last_error}")
