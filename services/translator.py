from __future__ import annotations

import json
import hashlib
import hmac
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from deep_translator import GoogleTranslator

from core.config import AppConfig
from core.errors import AppError


def translate_text(text: str, config: AppConfig) -> str:
    providers: dict[str, Callable[[str, AppConfig], str]] = {
        "microsoft": _translate_with_microsoft,
        "tencent": _translate_with_tencent,
        "baidu": _translate_with_baidu,
        "local_llm": _translate_with_local_llm,
    }
    translate = providers.get(config.translation_provider, _translate_with_google)
    attempts = max(1, int(config.translation_retry_count) + 1)
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return translate(text, config)
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(0.8 * attempt)

    raise AppError(f"翻译失败，已尝试 {attempts} 次：{last_error}")


def _translate_with_google(text: str, config: AppConfig) -> str:
    try:
        translated = GoogleTranslator(
            source=config.source_language,
            target=config.target_language,
        ).translate(text)
    except Exception as exc:
        raise AppError(f"谷歌翻译失败：{exc}") from exc

    if not translated:
        raise AppError("谷歌翻译失败：返回内容为空")
    return translated


_LANGUAGE_NAMES = {
    "zh-cn": "简体中文",
    "zh-hans": "简体中文",
    "zh-tw": "繁体中文",
    "zh-hant": "繁体中文",
    "zh": "简体中文",
    "ja": "日语",
    "en": "英语",
    "ko": "韩语",
    "ru": "俄语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
}

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _language_name(code: str) -> str:
    return _LANGUAGE_NAMES.get(code.strip().lower(), code.strip())


def _translate_with_local_llm(text: str, config: AppConfig) -> str:
    """本地 LLM 翻译。

    端点不带 /v1 时走 Ollama 原生 /api/chat（能用 think=false 关闭思考模式，速度快）；
    带 /v1 时走 OpenAI 兼容 /chat/completions（LM Studio / llama.cpp server 等）。
    """
    endpoint = config.local_llm_endpoint.strip().rstrip("/") or "http://127.0.0.1:11434"
    model = config.local_llm_model.strip()
    if not model:
        raise AppError("本地 LLM 翻译失败：请先在设置中填写模型名（如 qwen3.5:4b）")

    source = config.source_language.strip()
    source_name = "" if source.lower() == "auto" else _language_name(source)
    template = config.local_llm_prompt.strip() or (
        "你是翻译引擎。把用户发来的{source}内容翻译成{target}。"
        "只输出译文本身，不要解释、不要注音、不要重复原文。保持口语化、自然，符合日常对话语气。"
    )
    instruction = template.replace("{source}", source_name).replace("{target}", _language_name(config.target_language))

    messages = [
        {"role": "system", "content": instruction},
        {"role": "user", "content": text},
    ]
    use_openai_api = endpoint.endswith("/v1")
    if use_openai_api:
        # OpenAI 兼容接口无法可靠关闭思考模式，用 Qwen 的 /no_think 软开关兜底
        messages[0]["content"] += "/no_think"
        url = f"{endpoint}/chat/completions"
        body = {"model": model, "messages": messages, "temperature": 0.2, "stream": False}
    else:
        url = f"{endpoint}/api/chat"
        body = {
            "model": model,
            "messages": messages,
            "think": False,
            "stream": False,
            "keep_alive": "30m",  # 避免 Ollama 闲置 5 分钟就卸载模型，下次翻译又要等加载
            "options": {"temperature": 0.2},
        }

    headers = {"Content-Type": "application/json"}
    if config.local_llm_api_key.strip():
        headers["Authorization"] = f"Bearer {config.local_llm_api_key.strip()}"

    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    timeout = max(5, int(config.local_llm_timeout_seconds))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise AppError(
            f"本地 LLM 翻译失败：无法连接 {endpoint}（{exc.reason}）。"
            "请确认本地 LLM 服务已启动（如 Ollama），且模型已下载（如 ollama pull qwen3.5:4b）。"
        ) from exc
    except Exception as exc:
        raise AppError(f"本地 LLM 翻译失败：{exc}") from exc

    try:
        if use_openai_api:
            content = result["choices"][0]["message"]["content"]
        else:
            content = result["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AppError(f"本地 LLM 翻译失败：返回格式异常：{result}") from exc

    translated = _THINK_TAG_RE.sub("", str(content)).strip()
    if not translated:
        raise AppError("本地 LLM 翻译失败：返回内容为空")
    return translated


def _translate_with_microsoft(text: str, config: AppConfig) -> str:
    if not config.microsoft_translator_key:
        raise AppError("微软翻译失败：请先在设置中填写 Microsoft Translator Key")

    endpoint = config.microsoft_translator_endpoint.rstrip("/")
    query: dict[str, str] = {
        "api-version": "3.0",
        "to": config.target_language,
    }
    if config.source_language.lower() != "auto":
        query["from"] = config.source_language

    url = f"{endpoint}/translate?{urllib.parse.urlencode(query)}"
    body = json.dumps([{"text": text}], ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Ocp-Apim-Subscription-Key": config.microsoft_translator_key,
    }
    if config.microsoft_translator_region:
        headers["Ocp-Apim-Subscription-Region"] = config.microsoft_translator_region

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise AppError(f"微软翻译失败：{exc}") from exc

    try:
        translated = payload[0]["translations"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AppError(f"微软翻译失败：返回格式异常：{payload}") from exc

    if not translated:
        raise AppError("微软翻译失败：返回内容为空")
    return translated


def _translate_with_tencent(text: str, config: AppConfig) -> str:
    secret_id = config.tencent_translator_secret_id.strip()
    secret_key = config.tencent_translator_secret_key.strip()
    region = config.tencent_translator_region.strip() or "ap-guangzhou"
    if not secret_id or not secret_key:
        raise AppError("腾讯翻译失败：请先在设置中填写 SecretId 和 SecretKey")

    host = config.tencent_translator_endpoint.strip() or "tmt.tencentcloudapi.com"
    payload = json.dumps(
        {
            "SourceText": text,
            "Source": _normalize_tencent_language(config.source_language, is_source=True),
            "Target": _normalize_tencent_language(config.target_language, is_source=False),
            "ProjectId": 0,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    timestamp = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    service = "tmt"
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
    headers = {
        "Authorization": authorization,
        "Content-Type": "application/json; charset=utf-8",
        "Host": host,
        "X-TC-Action": "TextTranslate",
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": "2018-03-21",
        "X-TC-Region": region,
    }
    request = urllib.request.Request(
        f"https://{host}",
        data=payload.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise AppError(f"腾讯翻译失败：{exc}") from exc

    response_data = result.get("Response", {})
    if "Error" in response_data:
        error = response_data["Error"]
        code = error.get("Code", "")
        message = error.get("Message", "")
        if code == "AuthFailure.SecretIdNotFound":
            raise AppError(
                "腾讯翻译失败：SecretId 不存在。请在腾讯云访问管理/CAM 的 API 密钥页面复制 SecretId，"
                "不要填写 AppID、SecretKey 或其他 ID，并确认该密钥没有被删除。"
            )
        if code in {"AuthFailure.SignatureFailure", "AuthFailure.SignatureExpire"}:
            raise AppError(
                f"腾讯翻译失败：鉴权签名失败（{code}）。请检查 SecretId、SecretKey、系统时间和 Endpoint 设置。"
            )
        raise AppError(f"腾讯翻译失败：{code} {message}".strip())

    translated = response_data.get("TargetText", "")
    if not translated:
        raise AppError(f"腾讯翻译失败：返回内容为空：{result}")
    return translated


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _normalize_tencent_language(language: str, is_source: bool) -> str:
    normalized = language.strip()
    if is_source and normalized.lower() == "auto":
        return "auto"
    mapping = {
        "zh-cn": "zh",
        "zh-hans": "zh",
        "zh-tw": "zh-TW",
        "zh-hant": "zh-TW",
    }
    return mapping.get(normalized.lower(), normalized)


def _translate_with_baidu(text: str, config: AppConfig) -> str:
    app_id = config.baidu_translator_app_id.strip()
    secret_key = config.baidu_translator_secret_key.strip()
    if not app_id or not secret_key:
        raise AppError("百度翻译失败：请先在设置中填写 App ID 和密钥")

    salt = str(int(time.time() * 1000))
    source = _normalize_baidu_language(config.source_language, is_source=True)
    target = _normalize_baidu_language(config.target_language, is_source=False)
    sign_text = f"{app_id}{text}{salt}{secret_key}"
    sign = hashlib.md5(sign_text.encode("utf-8")).hexdigest()
    data = urllib.parse.urlencode(
        {
            "q": text,
            "from": source,
            "to": target,
            "appid": app_id,
            "salt": salt,
            "sign": sign,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        config.baidu_translator_endpoint.strip() or "https://fanyi-api.baidu.com/api/trans/vip/translate",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise AppError(f"百度翻译失败：{exc}") from exc

    if "error_code" in result:
        code = str(result.get("error_code", ""))
        message = result.get("error_msg", "")
        friendly = _baidu_error_message(code)
        raise AppError(f"百度翻译失败：{friendly}（{code} {message}）")

    try:
        translated = "\n".join(item["dst"] for item in result["trans_result"])
    except (KeyError, TypeError) as exc:
        raise AppError(f"百度翻译失败：返回格式异常：{result}") from exc

    if not translated:
        raise AppError(f"百度翻译失败：返回内容为空：{result}")
    return translated


def _normalize_baidu_language(language: str, is_source: bool) -> str:
    normalized = language.strip()
    if is_source and normalized.lower() == "auto":
        return "auto"
    mapping = {
        "zh-cn": "zh",
        "zh-hans": "zh",
        "zh-tw": "cht",
        "zh-hant": "cht",
        "ja": "jp",
    }
    return mapping.get(normalized.lower(), normalized)


def _baidu_error_message(code: str) -> str:
    messages = {
        "52001": "请求超时，请重试",
        "52002": "系统错误，请重试",
        "52003": "未授权用户，请检查 App ID 或密钥",
        "54000": "必填参数为空",
        "54001": "签名错误，请检查密钥",
        "54003": "访问频率受限，请稍后重试",
        "54004": "账户余额不足",
        "54005": "长 query 请求频繁，请稍后重试",
        "58000": "客户端 IP 非法，请检查百度翻译开放平台 IP 白名单",
        "58001": "译文语言方向不支持",
        "90107": "认证未通过或服务未开通",
    }
    return messages.get(code, "接口返回错误")
