from __future__ import annotations

import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw

from core.config import ConfigManager


class TrayApp:
    def __init__(self, config_manager: ConfigManager, show_input_callback, quit_callback, show_control_callback=None):
        self.config_manager = config_manager
        self.show_input_callback = show_input_callback
        self.quit_callback = quit_callback
        self.show_control_callback = show_control_callback
        self.icon: pystray.Icon | None = None

    def start(self) -> None:
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def stop(self) -> None:
        if self.icon is not None:
            self.icon.stop()

    def _run(self) -> None:
        image = Image.new("RGB", (64, 64), "#262a33")
        draw = ImageDraw.Draw(image)
        draw.ellipse((10, 10, 54, 54), fill="#4aa3ff")
        draw.text((23, 20), "T", fill="white")
        self.icon = pystray.Icon(
            "",
            image,
            "VRC VoiceBridge",
            menu=pystray.Menu(
                pystray.MenuItem("打开控制面板", lambda: self._open_control(), default=True, visible=self.show_control_callback is not None),
                pystray.MenuItem("打开输入框", lambda: self.show_input_callback()),
                pystray.MenuItem("打开网页设置", lambda: webbrowser.open(self._settings_url())),
                pystray.MenuItem("退出", lambda: self.quit_callback()),
            ),
        )
        self.icon.run()

    def _open_control(self) -> None:
        if self.show_control_callback is not None:
            self.show_control_callback()

    def _settings_url(self) -> str:
        config = self.config_manager.get()
        return f"http://{config.web_host}:{config.web_port}/"
