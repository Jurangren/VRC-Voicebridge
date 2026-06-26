"""图片翻译：抓取 VRChat 窗口画面（即头显视角），提交百度图片翻译 API，返回贴好译文的结果图。

为什么抓窗口而不截主显示器：SteamVR 不给外部应用合成器画面（takeStereoScreenshot 对后台
应用 RequestFailed、showMirrorWindow 已是空操作），而 VRChat 桌面窗口默认就是镜像头显视角，
用 PrintWindow(PW_RENDERFULLCONTENT) 能抓到它的真实画面，且与显示器位置无关。

凭证复用设置里"百度翻译配置"的 App ID / 密钥（文本与图片翻译通用），源语言用作译文目标语言
（把看到的外文翻成用户母语）。注意：图片翻译需在百度开放平台单独开通后该密钥才能调用。
"""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import io
import random

import requests
from PIL import Image, ImageGrab

from core.config import AppConfig
from core.errors import AppError

_PICTURE_ENDPOINT = "https://fanyi-api.baidu.com/api/trans/sdk/picture"
_CUID = "APICUID"
_MAC = "mac"
_PW_RENDERFULLCONTENT = 2
_DPI_PER_MONITOR_AWARE_V2 = wintypes.HANDLE(-4)

# 百度图片翻译的语言代码与项目内部代码的差异映射
_LANG_MAP = {"zh-cn": "zh", "zh_cn": "zh", "zh-hans": "zh", "ja": "jp", "ko": "kor"}

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32


class _BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


def _find_window(keyword: str) -> int | None:
    keyword = keyword.lower()
    match: list[int] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd):
            length = _user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buf, length + 1)
                if keyword in buf.value.lower():
                    match.append(hwnd)
        return True

    _user32.EnumWindows(proc(_cb), 0)
    return match[0] if match else None


def _capture_window(hwnd: int) -> Image.Image | None:
    # 在本线程内临时开启 per-monitor DPI 感知，保证拿到物理像素（不影响主线程/tkinter）
    old_ctx = None
    try:
        old_ctx = _user32.SetThreadDpiAwarenessContext(_DPI_PER_MONITOR_AWARE_V2)
    except Exception:
        old_ctx = None
    try:
        rect = wintypes.RECT()
        _user32.GetClientRect(hwnd, ctypes.byref(rect))
        width, height = rect.right, rect.bottom
        if width <= 0 or height <= 0:
            return None
        window_dc = _user32.GetWindowDC(hwnd)
        mem_dc = _gdi32.CreateCompatibleDC(window_dc)
        bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
        _gdi32.SelectObject(mem_dc, bitmap)
        result = _user32.PrintWindow(hwnd, mem_dc, _PW_RENDERFULLCONTENT)
        bmi = _BMIH()
        bmi.biSize = ctypes.sizeof(_BMIH)
        bmi.biWidth = width
        bmi.biHeight = -height  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        _gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(bmi), 0)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(mem_dc)
        _user32.ReleaseDC(hwnd, window_dc)
        if not result:
            return None
        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    finally:
        if old_ctx:
            try:
                _user32.SetThreadDpiAwarenessContext(old_ctx)
            except Exception:
                pass


def capture_vr_view(config: AppConfig) -> Image.Image:
    """抓取 VRChat（或配置的窗口关键字）的窗口画面 = 头显视角。关键字留空则退回截主显示器。"""
    keyword = (config.image_translate_window_keyword or "").strip()
    if not keyword:
        return ImageGrab.grab().convert("RGB")
    hwnd = _find_window(keyword)
    if hwnd is None:
        raise AppError(f"未找到窗口『{keyword}』：请确认 VRChat 正在运行（或在设置里改窗口关键字）")
    image = _capture_window(hwnd)
    if image is None:
        raise AppError(f"无法抓取窗口『{keyword}』画面（可能被最小化）")
    return image.convert("RGB")


def capture_primary_screen() -> Image.Image:
    """退路：截主显示器。"""
    return ImageGrab.grab().convert("RGB")


def _baidu_lang(code: str) -> str:
    code = (code or "").strip().lower()
    if not code:
        return "auto"
    return _LANG_MAP.get(code, code)


def translate_image(image: Image.Image, config: AppConfig) -> Image.Image:
    """调用百度图片翻译，返回贴好译文的结果图（PIL Image, RGB）。失败抛 AppError。"""
    appid = config.baidu_translator_app_id.strip()
    secret = config.baidu_translator_secret_key.strip()
    if not appid or not secret:
        raise AppError(
            "图片翻译失败：请先在 设置 → 百度翻译配置 填写 App ID 和密钥"
            "（图片翻译复用同一对密钥，并需在百度开放平台单独开通图片翻译）"
        )

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=90)
    image_bytes = buffer.getvalue()

    salt = str(random.randint(10000000, 99999999))
    image_md5 = hashlib.md5(image_bytes).hexdigest()
    sign = hashlib.md5(
        (appid + image_md5 + salt + _CUID + _MAC + secret).encode("utf-8")
    ).hexdigest()

    data = {
        # 图片里文字语言事先未知，固定 auto 自动检测；翻成 source_language（用户母语）
        "from": "auto",
        "to": _baidu_lang(config.source_language),
        "appid": appid,
        "salt": salt,
        "cuid": _CUID,
        "mac": _MAC,
        "version": "3",
        "paste": "1",  # 返回整张贴好译文的图片
        "sign": sign,
    }
    files = {"image": ("screen.jpg", image_bytes, "image/jpeg")}

    try:
        response = requests.post(_PICTURE_ENDPOINT, data=data, files=files, timeout=30)
        result = response.json()
    except Exception as exc:
        raise AppError(f"图片翻译请求失败：{exc}") from exc

    payload = result.get("data") or {}
    paste_b64 = payload.get("pasteImg")
    if not paste_b64:
        code = result.get("error_code")
        message = result.get("error_msg", "")
        raise AppError(
            f"图片翻译失败：{code} {message}".strip()
            + "（若提示未授权/未开通，请在百度翻译开放平台开通『图片翻译』服务）"
        )
    try:
        return Image.open(io.BytesIO(base64.b64decode(paste_b64))).convert("RGB")
    except Exception as exc:
        raise AppError(f"图片翻译结果解码失败：{exc}") from exc
