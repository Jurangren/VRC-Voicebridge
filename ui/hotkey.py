from __future__ import annotations

import keyboard

from core.errors import AppError


class HotkeyManager:
    def __init__(self, callback):
        self.callback = callback
        self._hotkey_handle = None
        self._release_hotkey_handle = None
        self._is_pressed = False
        self._hotkey = ""

    def register(self, hotkey: str, release_callback=None) -> None:
        self.unregister()
        try:
            if release_callback is not None:
                self._hotkey_handle = keyboard.hook(lambda _: self._handle_press_release(hotkey, release_callback))
            else:
                self._hotkey_handle = keyboard.add_hotkey(hotkey, self.callback)
            self._hotkey = hotkey
        except Exception as exc:
            raise AppError(f"注册全局热键 {hotkey} 失败：{exc}") from exc

    def _handle_press_release(self, hotkey: str, release_callback) -> None:
        try:
            pressed = keyboard.is_pressed(hotkey)
        except Exception:
            return
        if pressed and not self._is_pressed:
            self._is_pressed = True
            self.callback()
            return
        if not pressed and self._is_pressed:
            self._is_pressed = False
            release_callback()

    def unregister(self) -> None:
        if self._hotkey_handle is not None:
            try:
                if self._hotkey and self._release_hotkey_handle is None:
                    keyboard.remove_hotkey(self._hotkey_handle)
                else:
                    keyboard.unhook(self._hotkey_handle)
            except Exception:
                pass
        if self._release_hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self._release_hotkey_handle)
            except Exception:
                pass
        self._hotkey_handle = None
        self._release_hotkey_handle = None
        self._is_pressed = False
        self._hotkey = ""
