from __future__ import annotations

import time

from pythonosc.udp_client import SimpleUDPClient

from core.config import AppConfig
from core.errors import AppError

# 用户自己发送聊天框（文字输入/语音输入）的最近时间，用于压制收听翻译的聊天框显示
_LAST_USER_CHATBOX_AT = 0.0


def seconds_since_user_chatbox() -> float:
    return time.monotonic() - _LAST_USER_CHATBOX_AT


class VrcOscClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = SimpleUDPClient(config.osc_host, config.osc_port)

    def send_chatbox(self, message: str) -> None:
        global _LAST_USER_CHATBOX_AT
        try:
            self.client.send_message(
                self.config.osc_chatbox_path,
                [message, self.config.osc_chat_enter, self.config.osc_chat_notify],
            )
            _LAST_USER_CHATBOX_AT = time.monotonic()
        except Exception as exc:
            raise AppError(f"发送 VRChat 聊天气泡 OSC 失败：{exc}") from exc

    def send_listen_chatbox(self, message: str, hold_seconds: float) -> bool:
        """发送收听翻译到聊天框；用户自己的文本在 hold_seconds 内优先，期间跳过发送。

        返回是否真正发送。收听文本不触发通知音，避免刷屏打扰。
        """
        if seconds_since_user_chatbox() < max(0.0, float(hold_seconds)):
            return False
        try:
            self.client.send_message(
                self.config.osc_chatbox_path,
                [message, True, False],
            )
            return True
        except Exception as exc:
            raise AppError(f"发送收听翻译聊天框 OSC 失败：{exc}") from exc

    def set_typing(self, enabled: bool) -> None:
        try:
            self.client.send_message(self.config.osc_typing_path, bool(enabled))
        except Exception as exc:
            raise AppError(f"发送 VRChat 正在输入 OSC 失败：{exc}") from exc

    def set_voice(self, enabled: bool) -> None:
        try:
            self.client.send_message(self.config.osc_voice_path, bool(enabled))
        except Exception as exc:
            raise AppError(f"发送 VRChat 开麦/关麦 OSC 失败：{exc}") from exc
