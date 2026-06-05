from __future__ import annotations

import tkinter as tk
import time
from tkinter import ttk
from typing import Callable


class InputWindow:
    def __init__(
        self,
        root: tk.Tk,
        on_submit: Callable[[str, float], None],
        on_show: Callable[[], None] | None = None,
        on_hide: Callable[[], None] | None = None,
        on_submit_hide: Callable[[], None] | None = None,
    ):
        self.root = root
        self.on_submit = on_submit
        self.on_show = on_show
        self.on_hide = on_hide
        self.on_submit_hide = on_submit_hide
        self.window: tk.Toplevel | None = None
        self.entry: ttk.Entry | None = None

    def show(self, initial_text: str = "") -> None:
        self._notify_show()
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.set_text(initial_text)
            self._focus_entry()
            return

        self.window = tk.Toplevel(self.root)
        self.window.title("VRC VoiceBridge 输入")
        self.window.attributes("-topmost", True)
        self.window.resizable(False, False)
        self.window.geometry("520x92+420+260")
        self.window.protocol("WM_DELETE_WINDOW", self.hide)

        frame = ttk.Frame(self.window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="输入中文，回车后翻译成日文并发送到 VRChat：").pack(anchor="w")
        self.entry = ttk.Entry(frame, width=72)
        self.entry.pack(fill="x", pady=(8, 0))
        self.entry.bind("<Control-a>", self._select_all)
        self.entry.bind("<Control-A>", self._select_all)
        self.entry.bind("<Left>", self._move_to_selection_start)
        self.entry.bind("<Right>", self._move_to_selection_end)
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Escape>", lambda _event: self.hide())
        self.set_text(initial_text)
        self._focus_entry()

    def is_visible(self) -> bool:
        return self.window is not None and self.window.winfo_exists() and bool(self.window.winfo_viewable())

    def _focus_entry(self) -> None:
        if self.window is None or self.entry is None:
            return
        self.window.attributes("-topmost", True)
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.entry.focus_force()
        self.entry.icursor(tk.END)
        self.root.after(80, self._focus_entry_once)

    def _focus_entry_once(self) -> None:
        if self.window is not None and self.window.winfo_exists() and self.entry is not None:
            self.window.lift()
            self.window.focus_force()
            self.entry.focus_force()

    def set_text(self, text: str) -> None:
        if self.entry is None:
            return
        self.entry.delete(0, tk.END)
        if text:
            self.entry.insert(0, text)
        self.entry.icursor(tk.END)

    def _select_all(self, _event=None) -> str:
        if self.entry is None:
            return "break"
        self.entry.selection_range(0, tk.END)
        self.entry.icursor(tk.END)
        return "break"

    def _move_to_selection_start(self, _event=None) -> str | None:
        if self.entry is None:
            return None
        try:
            start = self.entry.index(tk.SEL_FIRST)
        except tk.TclError:
            return None
        self.entry.selection_clear()
        self.entry.icursor(start)
        return "break"

    def _move_to_selection_end(self, _event=None) -> str | None:
        if self.entry is None:
            return None
        try:
            end = self.entry.index(tk.SEL_LAST)
        except tk.TclError:
            return None
        self.entry.selection_clear()
        self.entry.icursor(end)
        return "break"

    def hide(self, notify: bool = True) -> None:
        if self.window is not None and self.window.winfo_exists():
            self.window.withdraw()
        if notify:
            self._notify_hide()

    def _notify_show(self) -> None:
        if self.on_show is not None:
            self.on_show()

    def _notify_hide(self) -> None:
        if self.on_hide is not None:
            self.on_hide()

    def _submit(self, _event=None) -> None:
        if self.entry is None:
            return
        started_at = time.perf_counter()
        text = self.entry.get().strip()
        self.entry.delete(0, tk.END)
        if self.on_submit_hide is not None:
            self.on_submit_hide()
        else:
            self.hide()
        if text:
            self.on_submit(text, started_at)
