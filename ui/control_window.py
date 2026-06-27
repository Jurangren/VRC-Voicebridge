"""桌面控制面板：web 设置页之外的第二个管理入口，常用操作一键可达。

与 InputWindow 一致走回调解耦：所有动作通过构造时传入的 callbacks 触发，
窗口本身只负责显示与刷新状态。所有回调都在 tkinter 主线程内调用（按钮事件 +
root.after 刷新），与 main.py 的单线程模型一致。
"""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Callable

from core.config import PRESET_COUNT, ConfigManager


class ControlWindow:
    REFRESH_MS = 1000

    def __init__(self, root: tk.Tk, config_manager: ConfigManager, callbacks: dict[str, Callable]):
        self.root = root
        self.config_manager = config_manager
        self.cb = callbacks
        self.window: tk.Toplevel | None = None
        self._refresh_job: str | None = None

        self._status_var = tk.StringVar(value="")
        self._realtime_btn: ttk.Button | None = None
        self._osc_btn: ttk.Button | None = None
        self._preset_combo: ttk.Combobox | None = None
        self._alpha_var = tk.DoubleVar(value=0.92)
        self._alpha_label_var = tk.StringVar(value="")
        self._preset_index_by_label: dict[str, int] = {}

    # ---------- 生命周期 ----------

    def show(self) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            self._refresh()
            return
        self._build()
        self._refresh()

    def hide(self) -> None:
        if self._refresh_job is not None:
            self.root.after_cancel(self._refresh_job)
            self._refresh_job = None
        if self.window is not None and self.window.winfo_exists():
            self.window.withdraw()

    # ---------- 构建 ----------

    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        self.window = win
        win.title("VRC VoiceBridge 控制面板")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.geometry("400x540+460+200")
        win.protocol("WM_DELETE_WINDOW", self.hide)

        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)

        # 状态
        status_box = ttk.LabelFrame(outer, text="状态", padding=10)
        status_box.pack(fill="x")
        ttk.Label(status_box, textvariable=self._status_var, justify="left").pack(anchor="w")

        # 快捷开关
        toggles = ttk.LabelFrame(outer, text="快捷开关", padding=10)
        toggles.pack(fill="x", pady=(12, 0))
        self._realtime_btn = ttk.Button(toggles, text="实时翻译", command=lambda: self._fire("toggle_realtime"))
        self._realtime_btn.pack(fill="x")
        self._osc_btn = ttk.Button(toggles, text="聊天框翻译显示", command=lambda: self._fire("toggle_osc"))
        self._osc_btn.pack(fill="x", pady=(8, 0))

        # 预设
        preset_box = ttk.LabelFrame(outer, text="预设", padding=10)
        preset_box.pack(fill="x", pady=(12, 0))
        self._preset_combo = ttk.Combobox(preset_box, state="readonly")
        self._preset_combo.pack(fill="x")
        row = ttk.Frame(preset_box)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="切换到此预设", command=self._apply_selected_preset).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="保存当前到此预设", command=self._save_selected_preset).pack(side="left", expand=True, fill="x", padx=(8, 0))

        # overlay 透明度
        alpha_box = ttk.LabelFrame(outer, text="字幕浮窗透明度", padding=10)
        alpha_box.pack(fill="x", pady=(12, 0))
        scale = ttk.Scale(alpha_box, from_=0.1, to=1.0, variable=self._alpha_var,
                          command=lambda _v: self._alpha_label_var.set(f"{self._alpha_var.get():.2f}"))
        scale.pack(fill="x")
        scale.bind("<ButtonRelease-1>", self._commit_alpha)
        ttk.Label(alpha_box, textvariable=self._alpha_label_var).pack(anchor="e")

        # 操作
        actions = ttk.LabelFrame(outer, text="操作", padding=10)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="打开输入框", command=lambda: self._fire("show_input")).pack(fill="x")
        ttk.Button(actions, text="图片翻译（VR）", command=lambda: self._fire("image_translate")).pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="打开网页设置", command=self._open_web).pack(fill="x", pady=(8, 0))

    # ---------- 动作 ----------

    def _fire(self, key: str) -> None:
        func = self.cb.get(key)
        if func is not None:
            func()
        self._refresh()

    def _apply_selected_preset(self) -> None:
        index = self._selected_preset_index()
        if index is not None:
            self.cb["apply_preset"](index)
            self._refresh()

    def _save_selected_preset(self) -> None:
        index = self._selected_preset_index()
        if index is not None:
            self.cb["save_preset"](index)
            self._refresh()

    def _selected_preset_index(self) -> int | None:
        if self._preset_combo is None:
            return None
        return self._preset_index_by_label.get(self._preset_combo.get())

    def _commit_alpha(self, _event=None) -> None:
        self.cb["set_overlay_alpha"](round(float(self._alpha_var.get()), 2))

    def _open_web(self) -> None:
        status = self.cb["status"]()
        webbrowser.open(status.get("web_url", ""))

    # ---------- 刷新 ----------

    def _refresh(self) -> None:
        if self.window is None or not self.window.winfo_exists():
            return
        status = self.cb["status"]()
        running = bool(status.get("realtime_running"))
        osc_on = bool(status.get("osc_enabled"))
        lines = [
            f"实时翻译：{'运行中' if running else '已停止'}",
            f"聊天框翻译显示：{'开' if osc_on else '关'}",
            f"当前预设：{status.get('preset_index')} · {status.get('preset_name', '')}",
            f"模式：{'VR overlay' if status.get('vr_mode') else '桌面浮窗'}",
        ]
        self._status_var.set("\n".join(lines))

        if self._realtime_btn is not None:
            self._realtime_btn.config(text=f"实时翻译：{'停止' if running else '开始'}")
        if self._osc_btn is not None:
            self._osc_btn.config(text=f"聊天框翻译显示：{'关闭' if osc_on else '开启'}")

        names = status.get("preset_names") or []
        if self._preset_combo is not None:
            labels = [f"{i + 1} · {name}" for i, name in enumerate(names[:PRESET_COUNT])]
            self._preset_index_by_label = {label: i + 1 for i, label in enumerate(labels)}
            if list(self._preset_combo["values"]) != labels:
                self._preset_combo["values"] = labels
            active = int(status.get("preset_index", 1))
            if 1 <= active <= len(labels):
                self._preset_combo.set(labels[active - 1])

        alpha = float(status.get("overlay_alpha", 0.92))
        self._alpha_var.set(alpha)
        self._alpha_label_var.set(f"{alpha:.2f}")

        self._refresh_job = self.root.after(self.REFRESH_MS, self._refresh)
