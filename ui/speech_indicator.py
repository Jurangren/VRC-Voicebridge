from __future__ import annotations

import ctypes
import time
import tkinter as tk


class SpeechIndicator:
    POLL_MS = 120
    SIZE = 64
    MARGIN = 18
    TRANSPARENT_COLOR = "#ff00ff"

    def __init__(self, root: tk.Tk, status_provider):
        self.root = root
        self.status_provider = status_provider
        self.window: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.text_window: tk.Toplevel | None = None
        self.text_frame: tk.Frame | None = None
        self.toast_window: tk.Toplevel | None = None
        self.toast_canvas: tk.Canvas | None = None
        self._toast_job: str | None = None
        self._poll_job: str | None = None
        self._pulse_job: str | None = None
        self._pulse = 0
        self._visible = False
        self._items: list[dict] = []
        self.text_position = "top"

    def toggle_text_position(self) -> None:
        self.root.after(0, self._toggle_text_position)

    def _toggle_text_position(self) -> None:
        self.text_position = "bottom" if self.text_position == "top" else "top"
        self._place_text_window()

    def start(self) -> None:
        if self._poll_job is None:
            self._poll()

    def stop(self) -> None:
        if self._poll_job is not None:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self.hide()

    def _poll(self) -> None:
        try:
            status = self.status_provider()
            speaking = bool(status.get("enabled")) and bool(status.get("speaking"))
        except Exception:
            speaking = False

        if speaking:
            self.show()
        else:
            self.hide()
        self._refresh_text_items()
        self._poll_job = self.root.after(self.POLL_MS, self._poll)

    def show_text(self, text: str, seconds: float, alpha: float = 0.78) -> None:
        self.root.after(0, lambda: self._show_text(text, seconds, alpha))

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
            self._draw_round_rect(self.toast_canvas, 1, 1, width - 2, height - 2, 16, fill="#111827", outline="#60a5fa")
            self.toast_canvas.create_text(18, 14, text=text, anchor="nw", fill="#e9f6ff", font=("Microsoft YaHei UI", 13, "bold"), width=width - 36)
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

    def _show_text(self, text: str, seconds: float, alpha: float) -> None:
        text = text.strip()
        if not text:
            return
        self._ensure_text_window()
        expire_at = time.monotonic() + max(1.0, float(seconds))
        bubble = tk.Canvas(
            self.text_frame,
            bg=self.TRANSPARENT_COLOR,
            highlightthickness=0,
            bd=0,
        )
        text_id = bubble.create_text(
            14,
            10,
            text=text,
            anchor="nw",
            fill="#e9f6ff",
            font=("Microsoft YaHei UI", 12, "bold"),
            width=620,
        )
        bbox = bubble.bbox(text_id) or (0, 0, 360, 32)
        width = min(max(bbox[2] + 28, 360), 680)
        height = max(bbox[3] + 20, 44)
        bubble.configure(width=width, height=height)
        self._draw_text_bubble(bubble, text, width, height, 1.0)
        bubble.pack(anchor="w", pady=(0, 8))
        item_alpha = min(max(float(alpha), 0.1), 1.0)
        self._items.append(
            {
                "label": bubble,
                "expire_at": expire_at,
                "alpha": item_alpha,
                "text": text,
                "width": width,
                "height": height,
            }
        )
        if self.text_window is not None:
            self.text_window.attributes("-alpha", item_alpha)
        self._place_text_window()
        if self.text_window is not None:
            self.text_window.deiconify()
            self.text_window.lift()

    def _ensure_text_window(self) -> None:
        if self.text_window is not None and self.text_window.winfo_exists():
            return
        self.text_window = tk.Toplevel(self.root)
        self.text_window.title("Speech Translation Text")
        self.text_window.overrideredirect(True)
        self.text_window.attributes("-topmost", True)
        self.text_window.attributes("-alpha", 0.94)
        self.text_window.configure(bg=self.TRANSPARENT_COLOR)
        try:
            self.text_window.wm_attributes("-transparentcolor", self.TRANSPARENT_COLOR)
        except tk.TclError:
            pass
        self.text_frame = tk.Frame(self.text_window, bg=self.TRANSPARENT_COLOR)
        self.text_frame.pack(fill="both", expand=True)
        self.text_window.withdraw()

    def _refresh_text_items(self) -> None:
        if not self._items:
            if self.text_window is not None and self.text_window.winfo_exists():
                self.text_window.withdraw()
            return
        now = time.monotonic()
        kept: list[dict] = []
        for item in self._items:
            label: tk.Canvas = item["label"]
            remaining = item["expire_at"] - now
            if remaining <= 0:
                label.destroy()
                continue
            if remaining < 0.8:
                fade = max(0.0, remaining / 0.8)
                self._draw_text_bubble(label, item["text"], item["width"], item["height"], fade)
            kept.append(item)
        self._items = kept
        self._place_text_window()
        if not self._items and self.text_window is not None and self.text_window.winfo_exists():
            self.text_window.withdraw()

    def _place_text_window(self) -> None:
        if self.text_window is None:
            return
        x, y = self.root.winfo_pointerxy()
        left, top, right, bottom = _monitor_rect_for_point(x, y)
        self.text_window.update_idletasks()
        width = min(max(self.text_window.winfo_reqwidth(), 360), 680)
        height = self.text_window.winfo_reqheight()
        if self.text_position == "bottom":
            x_pos = left + max((right - left - width) // 2, 0)
            y_pos = bottom - height - self.MARGIN - 296
        else:
            x_pos = left + self.MARGIN + self.SIZE + 12
            y_pos = top + self.MARGIN + 4
        self.text_window.geometry(f"{width}x{height}+{x_pos}+{y_pos}")

    def _draw_text_bubble(self, canvas: tk.Canvas, text: str, width: int, height: int, fade: float) -> None:
        fade = min(max(float(fade), 0.0), 1.0)
        canvas.delete("all")
        fill = _blend_color("#07111d", "#111827", fade)
        outline = _blend_color("#07111d", "#60a5fa", fade)
        text_color = _blend_color("#07111d", "#e9f6ff", fade)
        self._draw_round_rect(canvas, 1, 1, width - 2, height - 2, 14, fill=fill, outline=outline)
        canvas.create_text(
            14,
            10,
            text=text,
            anchor="nw",
            fill=text_color,
            font=("Microsoft YaHei UI", 12, "bold"),
            width=width - 28,
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

    def show(self) -> None:
        if not self._visible:
            self._ensure_window()
            self._visible = True
            if self.window is not None:
                self.window.deiconify()
                self.window.lift()
            self._pulse = 0
            self._animate()
        self._place_at_current_monitor_top_left()

    def hide(self) -> None:
        self._visible = False
        if self._pulse_job is not None:
            self.root.after_cancel(self._pulse_job)
            self._pulse_job = None
        if self.window is not None and self.window.winfo_exists():
            self.window.withdraw()

    def _ensure_window(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            return
        self.window = tk.Toplevel(self.root)
        self.window.title("Speech Indicator")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.92)
        self.window.configure(bg="#07111d")
        try:
            self.window.wm_attributes("-transparentcolor", "#07111d")
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(
            self.window,
            width=self.SIZE,
            height=self.SIZE,
            highlightthickness=0,
            bg="#07111d",
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.window.withdraw()

    def _animate(self) -> None:
        if not self._visible or self.canvas is None:
            return
        self.canvas.delete("all")
        phase = self._pulse % 12
        glow = 4 + abs(6 - phase)
        center = self.SIZE // 2
        radius = 22
        self.canvas.create_oval(
            center - radius - glow,
            center - radius - glow,
            center + radius + glow,
            center + radius + glow,
            outline="#3577f0",
            width=4,
        )
        self.canvas.create_oval(
            center - radius,
            center - radius,
            center + radius,
            center + radius,
            fill="#102033",
            outline="#8ed4ff",
            width=2,
        )
        self.canvas.create_text(center, center + 1, text="🎙", fill="#e9f6ff", font=("Segoe UI Emoji", 24))
        self._pulse += 1
        self._pulse_job = self.root.after(80, self._animate)

    def _place_at_current_monitor_top_left(self) -> None:
        if self.window is None:
            return
        x, y = self.root.winfo_pointerxy()
        left, top = _monitor_top_left_for_point(x, y)
        self.window.geometry(f"{self.SIZE}x{self.SIZE}+{left + self.MARGIN}+{top + self.MARGIN}")


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


def _monitor_top_left_for_point(x: int, y: int) -> tuple[int, int]:
    left, top, _right, _bottom = _monitor_rect_for_point(x, y)
    return left, top


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
