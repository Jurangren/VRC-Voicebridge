from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core.config import ConfigManager


class StatusOverlay:
    FADE_STEP_MS = 25
    FADE_STEPS = 8

    def __init__(self, root: tk.Tk, config_manager: ConfigManager):
        self.root = root
        self.config_manager = config_manager
        self.window: tk.Toplevel | None = None
        self.status_var = tk.StringVar(value="待机")
        self.progress_var = tk.StringVar(value="0/0")
        self._hide_job: str | None = None
        self._fade_job: str | None = None
        self.status_label: ttk.Label | None = None
        self.progress_label: ttk.Label | None = None

    def show_progress(self, step: int, total: int, message: str) -> None:
        self.root.after(0, lambda: self._show_progress(step, total, message))

    def show_warning(self, message: str, hide_after_ms: int = 2200) -> None:
        self.root.after(0, lambda: self._show_warning(message, hide_after_ms))

    def show_done(self, message: str = "完成", hide_after_ms: int = 2200) -> None:
        self.root.after(0, lambda: self._show_done(message, hide_after_ms))

    def show_error(self, message: str = "失败，请查看错误弹窗", hide_after_ms: int = 5000) -> None:
        self.root.after(0, lambda: self._show_error(message, hide_after_ms))

    def show_cancelled(self, message: str = "已取消", hide_after_ms: int = 2200) -> None:
        self.root.after(0, lambda: self._show_cancelled(message, hide_after_ms))

    def hide(self) -> None:
        self.root.after(0, self._hide)

    def _ensure_window(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            return

        self.window = tk.Toplevel(self.root)
        self.window.title("VRC TTS 状态")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.0)

        frame = ttk.Frame(self.window, padding=(14, 10))
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text="VRC TTS", font=("Microsoft YaHei UI", 10, "bold"))
        title.grid(row=0, column=0, sticky="w")

        self.progress_label = ttk.Label(frame, textvariable=self.progress_var, font=("Consolas", 10, "bold"))
        self.progress_label.grid(row=0, column=1, sticky="e", padx=(18, 0))

        self.status_label = ttk.Label(frame, textvariable=self.status_var, font=("Microsoft YaHei UI", 10))
        self.status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        frame.columnconfigure(0, weight=1)
        self._place_bottom_left()

    def _apply_alpha(self) -> None:
        if self.window is None:
            return
        self.window.attributes("-alpha", self._target_alpha())

    def _target_alpha(self) -> float:
        alpha = min(max(self.config_manager.get().overlay_alpha, 0.1), 1.0)
        return alpha

    def _set_text_color(self, color: str) -> None:
        if self.status_label is not None:
            self.status_label.configure(foreground=color)
        if self.progress_label is not None:
            self.progress_label.configure(foreground=color)

    def _place_bottom_left(self) -> None:
        if self.window is None:
            return
        self.window.update_idletasks()
        width = max(self.window.winfo_reqwidth(), 280)
        height = max(self.window.winfo_reqheight(), 72)
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        x = max((screen_width - width) // 2, 0)
        y = screen_height - height - 58
        self.window.geometry(f"{width}x{height}+{x}+{y}")

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

    def _show_progress(self, step: int, total: int, message: str) -> None:
        self._cancel_hide()
        self._ensure_window()
        self._set_text_color("#2ecc71")
        self.progress_var.set(f"{step}/{total}")
        self.status_var.set(message)
        self._place_bottom_left()
        self._show_window()

    def _show_done(self, message: str, hide_after_ms: int) -> None:
        self._ensure_window()
        self._set_text_color("#2ecc71")
        self.progress_var.set("完成")
        self.status_var.set(message)
        self._place_bottom_left()
        self._show_window()
        self._cancel_hide()
        self._hide_job = self.root.after(hide_after_ms, self._hide)

    def _show_error(self, message: str, hide_after_ms: int) -> None:
        self._ensure_window()
        self._set_text_color("#ff4d4f")
        self.progress_var.set("失败")
        self.status_var.set(message)
        self._place_bottom_left()
        self._show_window()
        self._cancel_hide()
        self._hide_job = self.root.after(hide_after_ms, self._hide)

    def _show_warning(self, message: str, hide_after_ms: int) -> None:
        self._ensure_window()
        self._set_text_color("#ff4d4f")
        self.progress_var.set("超时")
        self.status_var.set(message)
        self._place_bottom_left()
        self._show_window()
        self._cancel_hide()
        self._hide_job = self.root.after(hide_after_ms, self._hide)

    def _show_cancelled(self, message: str, hide_after_ms: int) -> None:
        self._ensure_window()
        self._set_text_color("#faad14")
        self.progress_var.set("取消")
        self.status_var.set(message)
        self._place_bottom_left()
        self._show_window()
        self._cancel_hide()
        self._hide_job = self.root.after(hide_after_ms, self._hide)

    def _hide(self) -> None:
        self._cancel_hide()
        if self.window is not None and self.window.winfo_exists():
            self._fade_to(0.0, on_done=self.window.withdraw)
