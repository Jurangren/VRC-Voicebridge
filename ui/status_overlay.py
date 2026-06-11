from __future__ import annotations

import tkinter as tk

from core.config import ConfigManager

BG = "#131826"
TEXT = "#e8edf6"
TEXT_DIM = "#8b97ad"
TRANSPARENT = "#ff00fe"

STATE_COLORS = {
    "progress": "#34d399",
    "done": "#34d399",
    "error": "#f87171",
    "warning": "#fbbf24",
    "cancelled": "#fbbf24",
}


class StatusOverlay:
    FADE_STEP_MS = 25
    FADE_STEPS = 8
    MIN_WIDTH = 300

    def __init__(self, root: tk.Tk, config_manager: ConfigManager):
        self.root = root
        self.config_manager = config_manager
        self.window: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self._hide_job: str | None = None
        self._fade_job: str | None = None
        self._state = "progress"
        self._badge = "0/0"
        self._message = "待机"

    def show_progress(self, step: int, total: int, message: str) -> None:
        self.root.after(0, lambda: self._show("progress", f"{step}/{total}", message))

    def show_warning(self, message: str, hide_after_ms: int = 2200) -> None:
        self.root.after(0, lambda: self._show("warning", "超时", message, hide_after_ms))

    def show_done(self, message: str = "完成", hide_after_ms: int = 2200) -> None:
        self.root.after(0, lambda: self._show("done", "完成", message, hide_after_ms))

    def show_error(self, message: str = "失败，请查看错误弹窗", hide_after_ms: int = 5000) -> None:
        self.root.after(0, lambda: self._show("error", "失败", message, hide_after_ms))

    def show_cancelled(self, message: str = "已取消", hide_after_ms: int = 2200) -> None:
        self.root.after(0, lambda: self._show("cancelled", "取消", message, hide_after_ms))

    def show_hint(self, message: str, hide_after_ms: int = 2200) -> None:
        self.root.after(0, lambda: self._show("progress", "提示", message, hide_after_ms))

    def hide(self) -> None:
        self.root.after(0, self._hide)

    def _show(self, state: str, badge: str, message: str, hide_after_ms: int | None = None) -> None:
        self._cancel_hide()
        self._state = state
        self._badge = badge
        self._message = str(message)
        self._ensure_window()
        self._render()
        self._show_window()
        if hide_after_ms is not None:
            self._hide_job = self.root.after(hide_after_ms, self._hide)

    def _ensure_window(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            return
        self.window = tk.Toplevel(self.root)
        self.window.title("VRC TTS 状态")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.0)
        self.window.configure(bg=TRANSPARENT)
        try:
            self.window.wm_attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self.window, highlightthickness=0, bd=0, bg=TRANSPARENT)
        self.canvas.pack(fill="both", expand=True)

    def _render(self) -> None:
        if self.canvas is None:
            return
        color = STATE_COLORS.get(self._state, STATE_COLORS["progress"])
        message_font = ("Microsoft YaHei UI", 10)
        self.canvas.delete("all")

        # 先放一次文字测量尺寸
        probe = self.canvas.create_text(0, 0, text=self._message, anchor="nw", font=message_font, width=420)
        bbox = self.canvas.bbox(probe) or (0, 0, 200, 18)
        self.canvas.delete(probe)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        width = max(self.MIN_WIDTH, text_width + 44)
        height = text_height + 52
        self.canvas.configure(width=width, height=height)

        self._draw_round_rect(self.canvas, 1, 1, width - 2, height - 2, 14, fill=BG, outline=color)
        self.canvas.create_rectangle(1, 16, 4, height - 16, fill=color, outline="")
        self.canvas.create_oval(16, 14, 25, 23, fill=color, outline="")
        self.canvas.create_text(32, 11, text="VRC TTS", anchor="nw", fill=TEXT_DIM, font=("Microsoft YaHei UI", 9, "bold"))
        self.canvas.create_text(width - 16, 11, text=self._badge, anchor="ne", fill=color, font=("Consolas", 10, "bold"))
        self.canvas.create_text(16, 36, text=self._message, anchor="nw", fill=TEXT, font=message_font, width=width - 32)
        self._place_bottom_center(width, height)

    @staticmethod
    def _draw_round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs) -> None:
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

    def _place_bottom_center(self, width: int, height: int) -> None:
        if self.window is None:
            return
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        x = max((screen_width - width) // 2, 0)
        y = screen_height - height - 58
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    def _target_alpha(self) -> float:
        return min(max(self.config_manager.get().overlay_alpha, 0.1), 1.0)

    def _cancel_hide(self) -> None:
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None

    def _cancel_fade(self) -> None:
        if self._fade_job is not None:
            self.root.after_cancel(self._fade_job)
            self._fade_job = None

    def _fade_to(self, target_alpha: float, on_done=None) -> None:
        if self.window is None or not self.window.winfo_exists():
            return
        self._cancel_fade()
        current_alpha = float(self.window.attributes("-alpha") or 0.0)
        delta = (target_alpha - current_alpha) / self.FADE_STEPS

        def step(index: int = 1) -> None:
            if self.window is None or not self.window.winfo_exists():
                return
            next_alpha = target_alpha if index >= self.FADE_STEPS else current_alpha + delta * index
            self.window.attributes("-alpha", max(0.0, min(next_alpha, self._target_alpha())))
            if index >= self.FADE_STEPS:
                self._fade_job = None
                if on_done is not None:
                    on_done()
                return
            self._fade_job = self.root.after(self.FADE_STEP_MS, lambda: step(index + 1))

        step()

    def _show_window(self) -> None:
        if self.window is None:
            return
        was_hidden = self.window.state() == "withdrawn" or float(self.window.attributes("-alpha") or 0.0) <= 0.0
        self.window.deiconify()
        self.window.lift()
        if was_hidden:
            self.window.attributes("-alpha", 0.0)
        self._fade_to(self._target_alpha())

    def _hide(self) -> None:
        self._cancel_hide()
        if self.window is not None and self.window.winfo_exists():
            self._fade_to(0.0, on_done=self.window.withdraw)
