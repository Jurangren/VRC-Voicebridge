from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import urllib.request
import uuid
from typing import Callable

import speech_recognition as sr

from core.config import AppConfig
from core.errors import AppError


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
            text = recognize_speech(audio, recognizer, self.config).strip()
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


def recognize_speech(audio: sr.AudioData, recognizer: sr.Recognizer, config: AppConfig) -> str:
    if config.speech_recognition_provider == "tencent":
        return _recognize_with_tencent(audio, config)
    return recognizer.recognize_google(audio, language=config.listen_language)


def _recognize_with_tencent(audio: sr.AudioData, config: AppConfig) -> str:
    secret_id = config.tencent_asr_secret_id.strip()
    secret_key = config.tencent_asr_secret_key.strip()
    if not secret_id or not secret_key:
        raise AppError("腾讯云语音识别失败：请先在设置中填写 SecretId 和 SecretKey")

    host = config.tencent_asr_endpoint.strip() or "asr.tencentcloudapi.com"
    region = config.tencent_asr_region.strip() or "ap-guangzhou"
    wav_data = audio.get_wav_data(convert_rate=16000, convert_width=2)
    payload = json.dumps(
        {
            "ProjectId": 0,
            "SubServiceType": 2,
            "EngSerViceType": config.tencent_asr_engine_model_type.strip() or "16k_zh",
            "SourceType": 1,
            "VoiceFormat": "wav",
            "UsrAudioKey": uuid.uuid4().hex,
            "Data": base64.b64encode(wav_data).decode("ascii"),
            "DataLen": len(wav_data),
            "FilterDirty": int(config.tencent_asr_filter_dirty),
            "FilterModal": int(config.tencent_asr_filter_modal),
            "FilterPunc": int(config.tencent_asr_filter_punc),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    service = "asr"
    algorithm = "TC3-HMAC-SHA256"
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            f"content-type:application/json; charset=utf-8\nhost:{host}\n",
            "content-type;host",
            hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        ]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            str(timestamp),
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, service)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders=content-type;host, Signature={signature}"
    )
    request = urllib.request.Request(
        f"https://{host}",
        data=payload.encode("utf-8"),
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-TC-Action": "SentenceRecognition",
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": "2019-06-14",
            "X-TC-Region": region,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise AppError(f"腾讯云语音识别失败：{exc}") from exc

    response_data = result.get("Response", {})
    if "Error" in response_data:
        error = response_data["Error"]
        code = error.get("Code", "")
        message = error.get("Message", "")
        raise AppError(f"腾讯云语音识别失败：{code} {message}".strip())

    text = response_data.get("Result", "")
    if not text:
        raise sr.UnknownValueError()
    return text


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
