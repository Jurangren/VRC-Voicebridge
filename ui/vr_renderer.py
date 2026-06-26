"""把各类浮窗内容绘制到一张透明 RGBA 画布上，供 SteamVR overlay 作为纹理推送。

设计上与桌面 tkinter 浮窗（speech_indicator / status_overlay）视觉一致：
字幕气泡在下方居中向上堆叠，双麦克风指示器在最下方居中，状态条在顶部居中，
预设 toast 在右上角。坐标系为画布像素，整张画布最终映射到 HMD 前方的一块四边形。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

# 画布尺寸（16:9），映射到 VR 中一块约 2.2m 宽的四边形
CANVAS_W = 1280
CANVAS_H = 720

# 颜色（与 tkinter 浮窗保持一致，改为 RGB 元组）
BUBBLE_BG = (19, 24, 38)
BUBBLE_BORDER = (79, 140, 255)
BUBBLE_TEXT = (232, 237, 246)
TEXT_DIM = (139, 151, 173)

STATE_COLORS = {
    "progress": (52, 211, 153),
    "done": (52, 211, 153),
    "error": (248, 113, 113),
    "warning": (251, 191, 36),
    "cancelled": (251, 191, 36),
}

# 与前端/realtime_pipeline 的 SPEAKER_COLORS 保持一致
SPEAKER_COLORS = [
    (79, 140, 255), (245, 158, 11), (52, 211, 153), (244, 114, 182),
    (167, 139, 250), (34, 211, 238), (251, 113, 133), (163, 230, 53),
]

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",  # 微软雅黑 Bold
    r"C:\Windows\Fonts\msyh.ttc",    # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",  # 黑体
]
_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def hex_to_rgb(value: str, fallback: tuple[int, int, int] = BUBBLE_BORDER) -> tuple[int, int, int]:
    value = (value or "").lstrip("#")
    if len(value) != 6:
        return fallback
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return fallback


def speaker_color_rgb(index: int) -> tuple[int, int, int]:
    return SPEAKER_COLORS[(index - 1) % len(SPEAKER_COLORS)]


def _font(size: int) -> ImageFont.FreeTypeFont:
    cached = _font_cache.get(size)
    if cached is not None:
        return cached
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _font_cache[size] = font
                return font
            except OSError:
                continue
    font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def _blend(start: tuple[int, int, int], end: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    ratio = min(max(ratio, 0.0), 1.0)
    return tuple(int(a + (b - a) * ratio) for a, b in zip(start, end))  # type: ignore[return-value]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """按像素宽度逐字符换行，兼容中日文（无空格）与英文。显式换行符保留。"""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            trial = current + ch
            if draw.textlength(trial, font=font) <= max_w or not current:
                current = trial
            else:
                lines.append(current)
                current = ch
        if current:
            lines.append(current)
    return lines


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return ascent + descent


def _draw_text_block(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], lines: list[str],
    font: ImageFont.FreeTypeFont, fill: tuple[int, int, int], line_gap: int = 4,
) -> None:
    x, y = xy
    lh = _line_height(font) + line_gap
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += lh


def _apply_alpha(image: Image.Image, alpha: float) -> None:
    """对整张已绘制图层按比例缩放 alpha 通道，实现渐隐。"""
    if alpha >= 1.0:
        return
    alpha = max(0.0, alpha)
    a = image.getchannel("A").point(lambda v: int(v * alpha))
    image.putalpha(a)


# ---------- 各浮窗元素 ----------

def _measure_bubble(draw: ImageDraw.ImageDraw, text: str, speaker_label: str, max_text_w: int) -> tuple[int, int, list[str], int]:
    font = _font(26)
    chip_w = 0
    if speaker_label:
        chip_font = _font(22)
        chip_w = int(draw.textlength(speaker_label, font=chip_font)) + 26
    lines = _wrap(draw, text, font, max_text_w - chip_w)
    text_w = max((int(draw.textlength(line, font=font)) for line in lines), default=0)
    lh = _line_height(font) + 4
    inner_h = max(lh * len(lines), lh)
    width = min(max(text_w + chip_w + 56, 280), max_text_w + 56)
    height = inner_h + 36
    return width, height, lines, chip_w


def _render_bubble(text: str, speaker_label: str, speaker_color: tuple[int, int, int], max_text_w: int) -> Image.Image:
    probe = Image.new("RGBA", (1, 1))
    pd = ImageDraw.Draw(probe)
    width, height, lines, chip_w = _measure_bubble(pd, text, speaker_label, max_text_w)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([1, 1, width - 2, height - 2], radius=16, fill=BUBBLE_BG + (255,), outline=speaker_color + (255,), width=2)

    text_x = 24
    if speaker_label and chip_w:
        chip_font = _font(22)
        chip_fill = _blend(BUBBLE_BG, speaker_color, 0.22)
        draw.rounded_rectangle([18, 14, 18 + chip_w, height - 14], radius=10, fill=chip_fill + (255,), outline=speaker_color + (255,), width=1)
        cy = (height - _line_height(chip_font)) // 2
        draw.text((18 + 13, cy), speaker_label, font=chip_font, fill=speaker_color)
        text_x = 18 + chip_w + 14

    _draw_text_block(draw, (text_x, 16), lines, _font(26), BUBBLE_TEXT)
    return img


def _render_status(state: str, badge: str, message: str) -> Image.Image:
    color = STATE_COLORS.get(state, STATE_COLORS["progress"])
    probe = Image.new("RGBA", (1, 1))
    pd = ImageDraw.Draw(probe)
    msg_font = _font(22)
    title_font = _font(20)
    max_text_w = 520
    lines = _wrap(pd, message, msg_font, max_text_w)
    text_w = max((int(pd.textlength(line, font=msg_font)) for line in lines), default=0)
    width = max(360, text_w + 56)
    lh = _line_height(msg_font) + 4
    height = lh * len(lines) + 72

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([1, 1, width - 2, height - 2], radius=16, fill=BUBBLE_BG + (255,), outline=color + (255,), width=2)
    draw.rectangle([1, 20, 5, height - 20], fill=color + (255,))
    draw.ellipse([22, 18, 34, 30], fill=color + (255,))
    draw.text((42, 14), "VRC VoiceBridge", font=title_font, fill=TEXT_DIM)
    badge_w = int(draw.textlength(badge, font=title_font))
    draw.text((width - 20 - badge_w, 14), badge, font=title_font, fill=color)
    _draw_text_block(draw, (22, 46), lines, msg_font, BUBBLE_TEXT)
    return img


def _draw_mic_indicator(draw: ImageDraw.ImageDraw, cx: int, cy: int, color: tuple[int, int, int], wave: float) -> None:
    r = 30
    glow_r = r + int(6 * wave)
    glow = _blend((26, 36, 56), color, 0.35 + 0.4 * wave)
    draw.ellipse([cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r], outline=glow + (255,), width=3)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BUBBLE_BG + (255,), outline=color + (255,), width=3)
    # 自绘麦克风：胶囊话筒 + U 形支架 + 立柱 + 底座
    body = _blend(color, (255, 255, 255), 0.25 + 0.35 * wave)
    draw.rounded_rectangle([cx - 8, cy - 20, cx + 8, cy + 4], radius=8, fill=body + (255,))
    draw.arc([cx - 14, cy - 10, cx + 14, cy + 14], start=0, end=180, fill=color + (255,), width=3)
    draw.line([cx, cy + 14, cx, cy + 20], fill=color + (255,), width=3)
    draw.line([cx - 8, cy + 21, cx + 8, cy + 21], fill=color + (255,), width=3)


# ---------- 合成 ----------

def render_composite(state: dict) -> Image.Image | None:
    """根据当前浮窗状态渲染整张画布；全部为空时返回 None（调用方据此隐藏 overlay）。"""
    subtitles = state.get("subtitles") or []
    indicators = state.get("indicators") or {}
    status = state.get("status")
    toast = state.get("toast")
    phase = float(state.get("phase", 0.0))

    has_indicator = bool(indicators.get("translate") or indicators.get("mic"))
    if not subtitles and not status and not toast and not has_indicator:
        return None

    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))

    # 双麦克风指示器：内容组最下方居中（蓝=翻译在左，绿=自己麦克风在右）
    if has_indicator:
        wave = abs(phase * 2 - 1)
        draw = ImageDraw.Draw(canvas)
        cy = 430
        gap = 8
        if indicators.get("translate"):
            _draw_mic_indicator(draw, CANVAS_W // 2 - 36 - gap, cy, (79, 140, 255), wave)
        if indicators.get("mic"):
            _draw_mic_indicator(draw, CANVAS_W // 2 + 36 + gap, cy, (52, 211, 153), wave)

    # 自下而上居中堆叠：字幕 -> 状态条 -> toast；内容锚在画布纵向中心附近，
    # 这样 overlay 的 vertical_m 直接等于"视野里往下多少"，调节直观、不会因内容偏底而移出视野
    subtitle_bottom = 400
    stack_top = subtitle_bottom

    # 字幕：贴底部居中，向上堆叠（最新在底部）
    if subtitles:
        max_text_w = 720
        rendered = []
        for item in subtitles:
            bub = _render_bubble(
                item["text"], item.get("speaker_label", ""),
                item.get("speaker_color", BUBBLE_BORDER), max_text_w,
            )
            _apply_alpha(bub, item.get("alpha", 1.0))
            rendered.append(bub)
        gap = 10
        total_h = sum(b.height for b in rendered) + gap * (len(rendered) - 1)
        y = subtitle_bottom - total_h
        stack_top = y
        for bub in rendered:
            x = (CANVAS_W - bub.width) // 2
            canvas.alpha_composite(bub, (max(x, 0), max(y, 0)))
            y += bub.height + gap

    # 状态条（语音识别提示）：居中，叠在字幕上方
    if status:
        st = _render_status(status["state"], status["badge"], status["message"])
        _apply_alpha(st, status.get("alpha", 1.0))
        sy = stack_top - 12 - st.height
        canvas.alpha_composite(st, ((CANVAS_W - st.width) // 2, max(sy, 0)))
        stack_top = sy

    # 预设 toast：居中，叠在最上方
    if toast:
        tb = _render_bubble(toast["text"], "", BUBBLE_BORDER, 360)
        _apply_alpha(tb, toast.get("alpha", 1.0))
        ty = stack_top - 10 - tb.height
        canvas.alpha_composite(tb, ((CANVAS_W - tb.width) // 2, max(ty, 0)))

    return canvas


# ---------- 图片翻译 overlay（独立一块，与字幕分开） ----------

def render_image_loading(phase: float) -> Image.Image:
    """翻译中：仅旋转圆环 + 文字（不再全屏蒙版，背景透明，只在局部托一个小底）。"""
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    cx, cy = CANVAS_W // 2, CANVAS_H // 2
    # 仅局部小底托住转圈和文字，保证任意背景下可读（不遮挡整个视野）
    draw.rounded_rectangle([cx - 130, cy - 110, cx + 130, cy + 110], radius=24, fill=(8, 11, 18, 170))
    r = 56
    ring_cy = cy - 14
    angle = (phase * 90.0) % 360.0
    draw.arc([cx - r, ring_cy - r, cx + r, ring_cy + r], start=0, end=360, fill=(60, 78, 110, 255), width=9)
    draw.arc([cx - r, ring_cy - r, cx + r, ring_cy + r], start=angle, end=angle + 100, fill=(120, 180, 255, 255), width=9)
    font = _font(30)
    text = "翻译中…"
    tw = int(draw.textlength(text, font=font))
    draw.text((cx - tw // 2, cy + 56), text, font=font, fill=BUBBLE_TEXT)
    return canvas


def render_image_panel(image: Image.Image) -> Image.Image:
    """把译文结果图等比缩放铺到画布中央，背景半透明深色。"""
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (8, 11, 18, 220))
    img = image.convert("RGBA")
    max_w, max_h = CANVAS_W - 32, CANVAS_H - 32
    scale = min(max_w / img.width, max_h / img.height)
    new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    img = img.resize(new_size)
    x = (CANVAS_W - img.width) // 2
    y = (CANVAS_H - img.height) // 2
    canvas.alpha_composite(img, (x, y))
    return canvas
