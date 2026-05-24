from __future__ import annotations

import traceback
from datetime import datetime
from threading import RLock


class AppError(Exception):
    pass


class ErrorHandler:
    def __init__(self, tk_root=None):
        self.tk_root = tk_root
        self._lock = RLock()
        self._last_error = ""

    def set_root(self, tk_root) -> None:
        self.tk_root = tk_root

    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def report(self, title: str, error: Exception | str, show_popup: bool = False) -> None:
        message = str(error)
        detail = traceback.format_exc() if isinstance(error, Exception) else message
        with self._lock:
            self._last_error = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {title}: {message}\n{detail}"
        print(self._last_error)

    def short_message(self, error: Exception | str, max_length: int = 80) -> str:
        message = str(error).replace("\r", " ").replace("\n", " ").strip()
        if len(message) <= max_length:
            return message
        return f"{message[:max_length - 1]}…"
