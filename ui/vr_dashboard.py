"""SteamVR 仪表盘（dashboard）overlay：按系统键调出 SteamVR 悬浮界面后，在底部点开本面板，
用手柄激光指针直接点开关、按上下箭头调数值（类似 OVR Toolkit / OVR Advanced Settings）。

实现方式：用 PIL 把一整块面板画成纹理推给 dashboard overlay，并把 overlay 的输入方式设为鼠标；
SteamVR 会把激光指针命中转成 VREvent_MouseMove / MouseButtonDown 事件，本类据此做命中检测、
执行对应动作并重绘。整个 tick 由 VROverlayUI 在主线程里调用，沿用单线程模型。
"""
from __future__ import annotations

import ctypes
import time

from PIL import Image, ImageDraw

from core.config import PRESET_COUNT, ConfigManager
from ui import vr_renderer as R

KEY = "vrc.voicebridge.dashboard"
NAME = "VRC VoiceBridge"

W, H = 920, 840

BG = (12, 16, 26)
ACCENT = (79, 140, 255)
GREEN = (52, 211, 153)
YELLOW = (251, 191, 36)
TEXT = (232, 237, 246)
DIM = (139, 151, 173)
BTN = (38, 46, 66)
BTN_HOVER = (58, 70, 98)
TRACK_OFF = (70, 78, 98)

# SteamVR 链接器状态 -> (显示文字, 颜色)
BRIDGE_LABELS = {
    "running": ("已启动", GREEN),
    "stopped": ("未启动", DIM),
    "starting": ("启动中…", YELLOW),
    "stopping": ("关闭中…", YELLOW),
}

# 上下箭头数值框可调的字段（直接落盘到 config，配置层会再做范围裁剪）
NUMERIC_FIELDS = [
    {"key": "overlay_alpha", "label": "字幕浮窗透明度", "min": 0.1, "max": 1.0, "step": 0.05, "digits": 2},
    {"key": "speech_translate_overlay_text_seconds", "label": "字幕停留秒数", "min": 1, "max": 30, "step": 1, "digits": 0},
    {"key": "image_translate_result_seconds", "label": "截图结果秒数", "min": 0, "max": 60, "step": 1, "digits": 0},
]

TOGGLES = [
    {"key": "realtime", "label": "实时翻译", "state_key": "realtime_running"},
    {"key": "osc", "label": "聊天框翻译显示", "state_key": "osc_enabled"},
]


class VRDashboard:
    def __init__(self, openvr_module, overlay, config_manager: ConfigManager, callbacks: dict, status_provider):
        self._openvr = openvr_module
        self._overlay = overlay
        self.config_manager = config_manager
        self.cb = callbacks
        self._status_provider = status_provider

        self._main = None
        self._thumb = None
        self._regions: list[dict] = []
        self._hover: str | None = None
        self._dirty = True
        self._last_render = 0.0
        self._last_data: bytes | None = None

        res = overlay.createDashboardOverlay(KEY, NAME)
        if isinstance(res, (tuple, list)):
            self._main = res[0]
            self._thumb = res[1] if len(res) > 1 else None
        else:
            self._main = res
        overlay.setOverlayWidthInMeters(self._main, 2.6)
        overlay.setOverlayInputMethod(self._main, self._openvr.VROverlayInputMethod_Mouse)
        scale = self._openvr.HmdVector2_t()
        scale.v[0] = float(W)
        scale.v[1] = float(H)
        overlay.setOverlayMouseScale(self._main, scale)

        self._set_thumbnail()
        self._render()  # 先画一帧，保证仪表盘里立刻有内容

    # ---------- 主循环（由 VROverlayUI._tick 在主线程调用）----------

    def tick(self) -> None:
        if self._main is None:
            return
        try:
            visible = bool(self._overlay.isOverlayVisible(self._main))
        except Exception:
            visible = False
        self._drain_events(visible)
        if not visible:
            self._hover = None
            return
        # 仪表盘可见时定期刷新，反映外部状态变化（如实时翻译被别处启停）
        if time.monotonic() - self._last_render >= 0.5:
            self._dirty = True
        if self._dirty:
            self._render()

    def _drain_events(self, visible: bool) -> None:
        event = self._openvr.VREvent_t()
        try:
            while self._overlay.pollNextOverlayEvent(self._main, event)[0]:
                if not visible:
                    continue
                et = event.eventType
                if et == self._openvr.VREvent_MouseMove:
                    self._on_move(event.data.mouse.x, event.data.mouse.y)
                elif et == self._openvr.VREvent_MouseButtonDown:
                    self._on_down(event.data.mouse.x, event.data.mouse.y)
        except Exception:
            pass

    # ---------- 鼠标命中 ----------

    def _to_pixels(self, mx: float, my: float) -> tuple[int, int]:
        # overlay 鼠标坐标原点在左下、y 向上；转成画布像素（左上、y 向下）
        return int(mx), int(H - my)

    def _region_at(self, px: int, py: int) -> dict | None:
        for region in self._regions:
            x0, y0, x1, y1 = region["rect"]
            if x0 <= px <= x1 and y0 <= py <= y1:
                return region
        return None

    def _on_move(self, mx: float, my: float) -> None:
        px, py = self._to_pixels(mx, my)
        region = self._region_at(px, py)
        key = region["key"] if region else None
        if key != self._hover:
            self._hover = key
            self._dirty = True

    def _on_down(self, mx: float, my: float) -> None:
        px, py = self._to_pixels(mx, my)
        region = self._region_at(px, py)
        if region is not None:
            try:
                region["action"]()
            except Exception:
                pass
            self._dirty = True

    # ---------- 动作 ----------

    def _toggle(self, key: str) -> None:
        if key == "realtime":
            self.cb["toggle_realtime"]()
        elif key == "osc":
            self.cb["toggle_osc"]()

    def _apply_preset(self, index: int) -> None:
        self.cb["apply_preset"](index)

    def _bump(self, field: dict, direction: int) -> None:
        current = float(getattr(self.config_manager.get(), field["key"]))
        value = current + direction * field["step"]
        value = min(max(value, field["min"]), field["max"])
        self.config_manager.patch_from_dict({field["key"]: value})

    # ---------- 渲染 ----------

    def _register(self, rect, key, action) -> None:
        self._regions.append({"rect": rect, "key": key, "action": action})

    def _render(self) -> None:
        status = self._status_provider()
        config = self.config_manager.get()
        img = Image.new("RGBA", (W, H), BG + (255,))
        draw = ImageDraw.Draw(img)
        self._regions = []

        draw.rounded_rectangle([8, 8, W - 8, H - 8], radius=20, outline=ACCENT + (255,), width=2)
        pad = 32
        draw.ellipse([pad, 30, pad + 20, 50], fill=ACCENT + (255,))
        draw.text((pad + 32, 24), "VRC VoiceBridge 控制台", font=R._font(32), fill=TEXT)
        draw.text((pad + 32, 64), "用手柄激光指针点击下面的开关与按钮", font=R._font(18), fill=DIM)

        y = 118
        for toggle in TOGGLES:
            on = bool(status.get(toggle["state_key"]))
            self._draw_toggle(draw, pad, y, W - pad, toggle["label"], on, toggle["key"])
            y += 74

        self._draw_bridge(draw, pad, y, W - pad)
        y += 80

        self._draw_preset(draw, pad, y, W - pad, status)
        y += 90

        for field in NUMERIC_FIELDS:
            value = float(getattr(config, field["key"]))
            self._draw_numeric(draw, pad, y, W - pad, field, value)
            y += 74

        y += 14
        self._draw_buttons(draw, pad, y, W - pad)

        self._submit(img)
        self._dirty = False
        self._last_render = time.monotonic()

    def _draw_toggle(self, draw, x0, y, x1, label, on, key) -> None:
        rowh = 58
        if self._hover == f"toggle:{key}":
            draw.rounded_rectangle([x0, y, x1, y + rowh], radius=12, fill=(26, 32, 48, 255))
        cy = y + rowh // 2
        draw.text((x0 + 14, cy - 18), label, font=R._font(28), fill=TEXT)
        pw, ph = 86, 40
        px1, py0, py1 = x1 - 14, cy - ph // 2, cy + ph // 2
        px0 = px1 - pw
        draw.rounded_rectangle([px0, py0, px1, py1], radius=ph // 2, fill=(GREEN if on else TRACK_OFF) + (255,))
        knob_r = ph // 2 - 5
        kcx = px1 - knob_r - 6 if on else px0 + knob_r + 6
        draw.ellipse([kcx - knob_r, cy - knob_r, kcx + knob_r, cy + knob_r], fill=(245, 248, 252, 255))
        txt = "开" if on else "关"
        tx = px0 - 16 - int(draw.textlength(txt, font=R._font(24)))
        draw.text((tx, cy - 15), txt, font=R._font(24), fill=(GREEN if on else DIM))
        self._register((x0, y, x1, y + rowh), f"toggle:{key}", lambda k=key: self._toggle(k))

    def _draw_bridge(self, draw, x0, y, x1) -> None:
        rowh = 60
        state = "stopped"
        getter = self.cb.get("bridge_status")
        if getter is not None:
            try:
                state = getter()
            except Exception:
                state = "stopped"
        text, color = BRIDGE_LABELS.get(state, BRIDGE_LABELS["stopped"])
        if self._hover == "bridge":
            draw.rounded_rectangle([x0, y, x1, y + rowh], radius=12, fill=(26, 32, 48, 255))
        cy = y + rowh // 2
        draw.text((x0 + 14, cy - 18), "SteamVR 链接器", font=R._font(28), fill=TEXT)
        # 右侧状态药丸，点击切换启动/停止
        pw = 168
        px1 = x1 - 14
        px0 = px1 - pw
        draw.rounded_rectangle([px0, cy - 24, px1, cy + 24], radius=16,
                               fill=BTN + (255,), outline=color + (255,), width=2)
        tw = int(draw.textlength(text, font=R._font(26)))
        draw.text(((px0 + px1) // 2 - tw // 2, cy - 17), text, font=R._font(26), fill=color)
        self._register((x0, y, x1, y + rowh), "bridge", lambda: self._toggle_bridge())

    def _toggle_bridge(self) -> None:
        toggle = self.cb.get("bridge_toggle")
        if toggle is not None:
            toggle()

    def _draw_preset(self, draw, x0, y, x1, status) -> None:
        rowh = 64
        draw.text((x0 + 14, y + rowh // 2 - 18), "预设", font=R._font(28), fill=TEXT)
        active = int(status.get("preset_index", 1))
        names = status.get("preset_names") or []
        name = names[active - 1] if 1 <= active <= len(names) else ""
        prev_idx = active - 1 if active > 1 else PRESET_COUNT
        next_idx = active + 1 if active < PRESET_COUNT else 1
        btn = 56
        # ◀
        rx1 = x1 - 14
        self._arrow_button(draw, rx1 - btn - 320 - btn, y, btn, rowh, "◀", "preset:prev",
                           lambda i=prev_idx: self._apply_preset(i))
        # name box
        nx0 = rx1 - btn - 320
        nx1 = rx1 - btn
        draw.rounded_rectangle([nx0, y + 6, nx1, y + rowh - 6], radius=10, fill=(24, 30, 46, 255), outline=(48, 58, 82, 255), width=1)
        label = f"{active} · {name}"
        lw = int(draw.textlength(label, font=R._font(26)))
        draw.text(((nx0 + nx1) // 2 - lw // 2, y + rowh // 2 - 17), label, font=R._font(26), fill=TEXT)
        # ▶
        self._arrow_button(draw, rx1 - btn, y, btn, rowh, "▶", "preset:next",
                           lambda i=next_idx: self._apply_preset(i))

    def _draw_numeric(self, draw, x0, y, x1, field, value) -> None:
        rowh = 60
        draw.text((x0 + 14, y + rowh // 2 - 17), field["label"], font=R._font(26), fill=TEXT)
        btn = 56
        rx1 = x1 - 14
        # [ - ]
        self._arrow_button(draw, rx1 - btn - 180 - btn, y, btn, rowh, "–", f"num:{field['key']}:-",
                           lambda f=field: self._bump(f, -1))
        # value box
        vx0 = rx1 - btn - 180
        vx1 = rx1 - btn
        draw.rounded_rectangle([vx0, y + 4, vx1, y + rowh - 4], radius=10, fill=(24, 30, 46, 255), outline=(48, 58, 82, 255), width=1)
        text = f"{value:.{field['digits']}f}"
        tw = int(draw.textlength(text, font=R._font(26)))
        draw.text(((vx0 + vx1) // 2 - tw // 2, y + rowh // 2 - 17), text, font=R._font(26), fill=TEXT)
        # [ + ]
        self._arrow_button(draw, rx1 - btn, y, btn, rowh, "+", f"num:{field['key']}:+",
                           lambda f=field: self._bump(f, +1))

    def _arrow_button(self, draw, x, y, w, h, glyph, key, action) -> None:
        hover = self._hover == key
        draw.rounded_rectangle([x, y + 4, x + w, y + h - 4], radius=10,
                               fill=(BTN_HOVER if hover else BTN) + (255,), outline=ACCENT + (255,), width=1)
        gw = int(draw.textlength(glyph, font=R._font(30)))
        draw.text((x + w // 2 - gw // 2, y + h // 2 - 20), glyph, font=R._font(30), fill=TEXT)
        self._register((x, y, x + w, y + h), key, action)

    def _draw_buttons(self, draw, x0, y, x1) -> None:
        gap = 18
        bw = (x1 - x0 - gap) // 2
        bh = 60
        self._text_button(draw, x0, y, bw, bh, "打开输入框", "act:input", lambda: self.cb["show_input"]())
        self._text_button(draw, x0 + bw + gap, y, bw, bh, "图片翻译", "act:image", lambda: self.cb["image_translate"]())

    def _text_button(self, draw, x, y, w, h, label, key, action) -> None:
        hover = self._hover == key
        draw.rounded_rectangle([x, y, x + w, y + h], radius=12,
                               fill=(BTN_HOVER if hover else BTN) + (255,), outline=ACCENT + (255,), width=2)
        lw = int(draw.textlength(label, font=R._font(26)))
        draw.text((x + w // 2 - lw // 2, y + h // 2 - 17), label, font=R._font(26), fill=TEXT)
        self._register((x, y, x + w, y + h), key, action)

    # ---------- overlay 推送 ----------

    def _submit(self, image: Image.Image) -> None:
        data = image.tobytes()
        if data == self._last_data:
            return
        buffer = (ctypes.c_char * len(data)).from_buffer_copy(data)
        self._overlay.setOverlayRaw(self._main, buffer, image.width, image.height, 4)
        self._last_data = data

    def _set_thumbnail(self) -> None:
        if self._thumb is None:
            return
        try:
            size = 128
            icon = Image.new("RGBA", (size, size), (18, 24, 38, 255))
            draw = ImageDraw.Draw(icon)
            draw.rounded_rectangle([6, 6, size - 6, size - 6], radius=20, outline=ACCENT + (255,), width=4)
            text = "VB"
            tw = int(draw.textlength(text, font=R._font(60)))
            draw.text((size // 2 - tw // 2, 30), text, font=R._font(60), fill=TEXT)
            data = icon.tobytes()
            buffer = (ctypes.c_char * len(data)).from_buffer_copy(data)
            self._overlay.setOverlayRaw(self._thumb, buffer, size, size, 4)
        except Exception:
            pass
