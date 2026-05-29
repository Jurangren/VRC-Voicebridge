from __future__ import annotations

from pythonosc.udp_client import SimpleUDPClient

from core.config import AppConfig
from core.errors import AppError


class VrcOscClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.client = SimpleUDPClient(config.osc_host, config.osc_port)

    def send_chatbox(self, message: str) -> None:
        try:
            self.client.send_message(
                self.config.osc_chatbox_path,
                [message, self.config.osc_chat_enter, self.config.osc_chat_notify],
            )
        except Exception as exc:
            raise AppError(f"发送 VRChat 聊天气泡 OSC 失败：{exc}") from exc

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
