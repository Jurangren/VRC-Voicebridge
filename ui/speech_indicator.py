from __future__ import annotations

import ctypes
import time
import tkinter as tk
import tkinter.font as tkfont

BUBBLE_BG = "#131826"
BUBBLE_BORDER = "#4f8cff"
BUBBLE_TEXT = "#e8edf6"
TRANSPARENT = "#07111d"

# 双说话指示器：左蓝=实时翻译听到的声音，右绿=自己麦克风 VAD 录音
INDICATOR_CHANNELS = {
    "translate": {"color": "#4f8cff", "body_light": "#9cc1ff", "accent": "#aac6ff", "slot": -1},
    "mic": {"color": "#34d399", "body_light": "#8ef0c8", "accent": "#a7f3d0", "slot": 1},
}


class SpeechIndicator:
    POLL_MS = 120
    SIZE = 64
    MARGIN = 18
    TRANSPARENT_COLOR = "#ff00ff"

    def __init__(self, root: tk.Tk, status_provider):
        self.root = root
        self.status_provider = status_provider
        self._channels: dict[str, dict] = {
            name: {"window": None, "canvas": None, "visible": False} for name in INDICATOR_CHANNELS
        }
        self.toast_window: tk.Toplevel | None = None
        self.toast_canvas: tk.Canvas | None = None
        self._toast_job: str | None = None
        self._poll_job: str | None = None
        self._pulse_job: str | None = None
        self._pulse = 0
        self._items: list[dict] = []

    def start(self) -> None:
        if self._poll_job is None:
            self._poll()

    def stop(self) -> None:
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        for name in self._channels:
            self._hide_channel(name)

    def _poll(self) -> None:
        try:
            status = self.status_provider()
            translate_speaking = bool(status.get("translate_speaking"))
            mic_speaking = bool(status.get("mic_speaking"))
            # 兼容旧的 {"enabled", "speaking"} 状态格式
            if "translate_speaking" not in status:
                translate_speaking = bool(status.get("enabled")) and bool(status.get("speaking"))
        except Exception:
            translate_speaking = mic_speaking = False

        self._set_channel_visible("translate", translate_speaking)
        self._set_channel_visible("mic", mic_speaking)
        self._refresh_text_items()
        self._poll_job = self.root.after(self.POLL_MS, self._poll)

    def _set_channel_visible(self, name: str, visible: bool) -> None:
        if visible:
            self._show_channel(name)
        else:
            self._hide_channel(name)

    def show_text(
        self,
        text: str,
        seconds: float,
        alpha: float = 0.78,
        speaker_label: str = "",
        speaker_color: str = "",
    ) -> None:
        self.root.after(0, lambda: self._show_text(text, seconds, alpha, speaker_label, speaker_color))

    def show_toast(self, text: str, seconds: float = 2.2) -> None:
        self.root.after(0, lambda: self._show_toast(text, seconds))

    def _show_toast(self, text: str, seconds: float) -> None:
        text = str(text).strip()
        if not text:
            return
        self._ensure_toast_window()
        if self._toast_job is not None:
            self.root.after_cancel(self._toast_job)
            self._toast_job = None
        width, height = 340, 76
        if self.toast_canvas is not None:
            self.toast_canvas.configure(width=width, height=height)
            self.toast_canvas.delete("all")
            self._draw_round_rect(self.toast_canvas, 1, 1, width - 2, height - 2, 16, fill=BUBBLE_BG, outline=BUBBLE_BORDER)
            self.toast_canvas.create_rectangle(1, 18, 4, height - 18, fill=BUBBLE_BORDER, outline="")
            self.toast_canvas.create_text(18, 14, text=text, anchor="nw", fill=BUBBLE_TEXT, font=("Microsoft YaHei UI", 13, "bold"), width=width - 36)
        self._place_toast_window(width, height)
        if self.toast_window is not None:
            self.toast_window.deiconify()
            self.toast_window.lift()
        self._toast_job = self.root.after(int(max(0.5, float(seconds)) * 1000), self._hide_toast)

    def _ensure_toast_window(self) -> None:
        if self.toast_window is not None and self.toast_window.winfo_exists():
            return
        self.toast_window = tk.Toplevel(self.root)
        self.toast_window.title("Preset Toast")
        self.toast_window.overrideredirect(True)
        self.toast_window.attributes("-topmost", True)
        self.toast_window.attributes("-alpha", 0.94)
        self.toast_window.configure(bg=self.TRANSPARENT_COLOR)
        try:
            self.toast_window.wm_attributes("-transparentcolor", self.TRANSPARENT_COLOR)
        except tk.TclError:
            pass
        self.toast_canvas = tk.Canvas(self.toast_window, highlightthickness=0, bd=0, bg=self.TRANSPARENT_COLOR)
        self.toast_canvas.pack(fill="both", expand=True)
        self.toast_window.withdraw()

    def _place_toast_window(self, width: int, height: int) -> None:
        if self.toast_window is None:
            return
        x, y = self.root.winfo_pointerxy()
        _left, top, right, _bottom = _monitor_rect_for_point(x, y)
        self.toast_window.geometry(f"{width}x{height}+{right - width - self.MARGIN}+{top + self.MARGIN}")

    def _hide_toast(self) -> None:
        self._toast_job = None
        if self.toast_window is not None and self.toast_window.winfo_exists():
            self.toast_window.withdraw()

    def _show_text(self, text: str, seconds: float, alpha: float, speaker_label: str = "", speaker_color: str = "") -> None:
        text = text.strip()
        if not text:
            return
        # 每条气泡一个独立窗口，淡出时直接降低窗口透明度，让整个气泡一起渐隐
        window = tk.Toplevel(self.root)
        window.title("Speech Translation Text")
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=self.TRANSPARENT_COLOR)
        window.withdraw()
        try:
            window.wm_attributes("-transparentcolor", self.TRANSPARENT_COLOR)
        except tk.TclError:
            pass
        bubble = tk.Canvas(window, bg=self.TRANSPARENT_COLOR, highlightthickness=0, bd=0)
        bubble.pack(fill="both", expand=True)
        chip_width = self._chip_width(bubble, speaker_label)
        text_x = 14 + (chip_width + 10 if chip_width else 0)
        text_id = bubble.create_text(
            text_x,
            10,
            text=text,
            anchor="nw",
            fill=BUBBLE_TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
            width=620 - chip_width,
        )
        bbox = bubble.bbox(text_id) or (0, 0, 360, 32)
        width = min(max(bbox[2] + 28, 360), 680)
        height = max(bbox[3] + 20, 44)
        bubble.configure(width=width, height=height)
        item = {
            "window": window,
            "label": bubble,
            "expire_at": time.monotonic() + max(1.0, float(seconds)),
            "alpha": min(max(float(alpha), 0.1), 1.0),
            "text": text,
            "width": width,
            "height": height,
            "speaker_label": speaker_label,
            "speaker_color": speaker_color or BUBBLE_BORDER,
            "chip_width": chip_width,
        }
        self._draw_text_bubble(item)
        window.attributes("-alpha", item["alpha"])
        self._items.append(item)
        self._place_bubbles()
        window.deiconify()
        window.lift()

    def _refresh_text_items(self) -> None:
        if not self._items:
            return
        now = time.monotonic()
        kept: list[dict] = []
        for item in self._items:
            window: tk.Toplevel = item["window"]
            remaining = item["expire_at"] - now
            if remaining <= 0:
                window.destroy()
                continue
            if remaining < 0.8:
                fade = max(0.0, remaining / 0.8)
                try:
                    window.attributes("-alpha", item["alpha"] * fade)
                except tk.TclError:
                    pass
            kept.append(item)
        self._items = kept
        self._place_bubbles()

    def _place_bubbles(self) -> None:
        """气泡固定显示在屏幕下方居中区域，新气泡在下、整体向上生长。"""
        if not self._items:
            return
        x, y = self.root.winfo_pointerxy()
        left, _top, right, bottom = _monitor_rect_for_point(x, y)
        gap = 8
        total_height = sum(item["height"] for item in self._items) + gap * (len(self._items) - 1)
        y_pos = bottom - self.MARGIN - 296 - total_height
        for item in self._items:
            x_pos = left + max((right - left - item["width"]) // 2, 0)
            try:
                item["window"].geometry(f"{item['width']}x{item['height']}+{x_pos}+{y_pos}")
            except tk.TclError:
                pass
            y_pos += item["height"] + gap

    @staticmethod
    def _chip_width(canvas: tk.Canvas, speaker_label: str) -> int:
        if not speaker_label:
            return 0
        font = tkfont.Font(family="Microsoft YaHei UI", size=10, weight="bold")
        return font.measure(speaker_label) + 16

    def _draw_text_bubble(self, item: dict) -> None:
        canvas: tk.Canvas = item["label"]
        width, height = item["width"], item["height"]
        speaker_label = item.get("speaker_label", "")
        speaker_color = item.get("speaker_color", BUBBLE_BORDER)
        chip_width = item.get("chip_width", 0)
        canvas.delete("all")
        self._draw_round_rect(canvas, 1, 1, width - 2, height - 2, 14, fill=BUBBLE_BG, outline=speaker_color)
        text_x = 14
        if speaker_label and chip_width:
            chip_fill = _blend_color(BUBBLE_BG, speaker_color, 0.22)
            self._draw_round_rect(canvas, 12, 9, 12 + chip_width, 31, 10, fill=chip_fill, outline=speaker_color)
            canvas.create_text(
                12 + chip_width / 2,
                20,
                text=speaker_label,
                fill=speaker_color,
                font=("Microsoft YaHei UI", 10, "bold"),
            )
            text_x = 14 + chip_width + 10
        canvas.create_text(
            text_x,
            10,
            text=item["text"],
            anchor="nw",
            fill=BUBBLE_TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
            width=620 - chip_width,
        )

    def _draw_round_rect(self, canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs) -> None:
        points = [
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        ]
        canvas.create_polygon(points, smooth=True, splinesteps=18, **kwargs)

    def _show_channel(self, name: str) -> None:
        channel = self._channels[name]
        if not channel["visible"]:
            self._ensure_channel_window(name)
            channel["visible"] = True
            channel["window"].deiconify()
            channel["window"].lift()
            if self._pulse_job is None:
                self._pulse = 0
                self._animate()
        self._place_channels()

    def _hide_channel(self, name: str) -> None:
        channel = self._channels[name]
        channel["visible"] = False
        window = channel["window"]
        if window is not None and window.winfo_exists():
            window.withdraw()
        if not any(item["visible"] for item in self._channels.values()) and self._pulse_job is not None:
            self.root.after_cancel(self._pulse_job)
            self._pulse_job = None

    def _ensure_channel_window(self, name: str) -> None:
        channel = self._channels[name]
        if channel["window"] is not None and channel["window"].winfo_exists():
            return
        window = tk.Toplevel(self.root)
        window.title(f"Speech Indicator ({name})")
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.92)
        window.configure(bg="#07111d")
        try:
            window.wm_attributes("-transparentcolor", "#07111d")
        except tk.TclError:
            pass
        canvas = tk.Canvas(window, width=self.SIZE, height=self.SIZE, highlightthickness=0, bg="#07111d", bd=0)
        canvas.pack(fill="both", expand=True)
        window.withdraw()
        channel["window"] = window
        channel["canvas"] = canvas

    def _animate(self) -> None:
        visible_any = False
        phase = (self._pulse % 16) / 16.0
        wave = abs(phase * 2 - 1)  # 0 -> 1 -> 0 的呼吸曲线
        for name, channel in self._channels.items():
            if not channel["visible"] or channel["canvas"] is None:
                continue
            visible_any = True
            self._draw_mic(channel["canvas"], INDICATOR_CHANNELS[name], wave)
        if not visible_any:
            self._pulse_job = None
            return
        self._pulse += 1
        self._pulse_job = self.root.after(80, self._animate)

    def _draw_mic(self, canvas: tk.Canvas, palette: dict, wave: float) -> None:
        canvas.delete("all")
        center = self.SIZE // 2
        color = palette["color"]

        # 呼吸光环
        glow_radius = 24 + int(4 * wave)
        glow_color = _blend_color("#1a2438", color, 0.35 + 0.4 * wave)
        canvas.create_oval(
            center - glow_radius, center - glow_radius, center + glow_radius, center + glow_radius,
            outline=glow_color, width=2,
        )
        # 深色圆底
        canvas.create_oval(center - 22, center - 22, center + 22, center + 22, fill=BUBBLE_BG, outline=color, width=2)

        # 自绘麦克风：胶囊话筒 + U 形支架 + 立柱 + 底座
        body_color = _blend_color(color, palette["body_light"], 0.35 + 0.4 * wave)
        accent = palette["accent"]
        canvas.create_oval(center - 6, center - 15, center + 6, center - 3, fill=body_color, outline="")
        canvas.create_rectangle(center - 6, center - 9, center + 6, center + 1, fill=body_color, outline="")
        canvas.create_oval(center - 6, center - 5, center + 6, center + 7, fill=body_color, outline="")
        canvas.create_arc(
            center - 10, center - 6, center + 10, center + 12,
            start=180, extent=180, style="arc", outline=accent, width=2,
        )
        canvas.create_line(center, center + 12, center, center + 16, fill=accent, width=2)
        canvas.create_line(center - 6, center + 17, center + 6, center + 17, fill=accent, width=2, capstyle="round")

    def _place_channels(self) -> None:
        """双指示器固定在屏幕正下方居中：蓝色（实时翻译）在左、绿色（自己录音）在右。"""
        x, y = self.root.winfo_pointerxy()
        left, _top, right, bottom = _monitor_rect_for_point(x, y)
        x_center = left + (right - left) // 2
        y_pos = bottom - self.SIZE - 150
        gap = 6
        for name, channel in self._channels.items():
            window = channel["window"]
            if window is None or not window.winfo_exists():
                continue
            slot = INDICATOR_CHANNELS[name]["slot"]
            x_pos = x_center - self.SIZE - gap if slot < 0 else x_center + gap
            window.geometry(f"{self.SIZE}x{self.SIZE}+{x_pos}+{y_pos}")


def _blend_color(start: str, end: str, ratio: float) -> str:
    ratio = min(max(float(ratio), 0.0), 1.0)
    start = start.lstrip("#")
    end = end.lstrip("#")
    values = []
    for index in range(0, 6, 2):
        a = int(start[index:index + 2], 16)
        b = int(end[index:index + 2], 16)
        values.append(int(a + (b - a) * ratio))
    return f"#{values[0]:02x}{values[1]:02x}{values[2]:02x}"


def _monitor_rect_for_point(x: int, y: int) -> tuple[int, int, int, int]:
    try:
        user32 = ctypes.windll.user32
        monitor = user32.MonitorFromPoint(wintypes_POINT(x, y), 2)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom
    except Exception:
        pass
    return 0, 0, 1920, 1080


class wintypes_POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    ]
